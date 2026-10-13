"""Бизнес-логика бронирования.

Все изменения броней, баллов и связанных платежей идут через эти функции. Время `now` можно передать
явно (в тестах), по умолчанию берётся текущее.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.bonuses.models import BonusTransaction
from apps.catalog.models import Extra, Location
from apps.core.models import StudioSettings
from apps.payments.models import Payment
from apps.users.models import User

from .exceptions import (
    BonusLimitExceeded,
    CancellationNotAllowed,
    ExtraUnavailable,
    InvalidBookingState,
    InvalidTimeRange,
    LocationUnavailable,
    SlotUnavailable,
)
from .models import Booking, BookingExtra
from .pricing import HOUR, PriceQuote, calculate_price, hour_price, iter_hours, validate_window, working_window

OVERLAP_CONSTRAINT = "booking_no_overlap_in_location"


@dataclass(frozen=True)
class ExtraOrder:
    """Запрос на допуслугу. `hours=None` — на всю длительность брони."""

    extra_id: int
    hours: int | None = None


@dataclass(frozen=True)
class BookingQuote:
    location: Location
    starts_at: datetime
    ends_at: datetime
    price: PriceQuote
    bonus_points: int
    total: Decimal
    max_bonus_points: int


@dataclass(frozen=True)
class HourSlot:
    starts_at: datetime
    ends_at: datetime
    is_available: bool
    price: Decimal


def _notify_after_commit(task_name: str, booking_id: int) -> None:
    """Письмо уходит только после успешной фиксации транзакции."""

    def send():
        from . import tasks

        getattr(tasks, task_name).delay(booking_id)

    transaction.on_commit(send)


def _blocking_q(now: datetime, prefix: str = "") -> Q:
    """Брони, которые занимают слот: подтверждённые и неоплаченные с живым холдом."""
    confirmed = Q(**{f"{prefix}status__in": [Booking.Status.CONFIRMED, Booking.Status.COMPLETED]})
    held = Q(**{f"{prefix}status": Booking.Status.PENDING}) & (
        Q(**{f"{prefix}expires_at__isnull": True}) | Q(**{f"{prefix}expires_at__gt": now})
    )
    return confirmed | held


def _location_group_ids(location: Location) -> list[int]:
    related = location.conflicts_with.values_list("pk", flat=True)
    return sorted({location.pk, *related})


def _cashback_points(total: Decimal, settings: StudioSettings) -> int:
    return int((total * settings.cashback_percent / 100).to_integral_value(ROUND_FLOOR))


def max_spendable_bonus(user: User, subtotal: Decimal, settings: StudioSettings | None = None) -> int:
    """Сколько баллов клиент может потратить на бронь стоимостью `subtotal`."""
    settings = settings or StudioSettings.load()
    share_cap = int((subtotal * settings.max_bonus_share_percent / 100).to_integral_value(ROUND_FLOOR))
    return max(0, min(user.bonus_balance, share_cap, int(subtotal)))


def get_day_slots(location: Location, day: date, *, now: datetime | None = None) -> list[HourSlot]:
    """Часовые слоты зала на дату с признаком доступности и ценой часа."""
    now = now or timezone.now()
    opens, closes = working_window(location, day)
    busy = list(
        Booking.objects.filter(location_id__in=_location_group_ids(location), starts_at__lt=closes, ends_at__gt=opens)
        .filter(_blocking_q(now))
        .values_list("starts_at", "ends_at")
    )
    slots = []
    for start in iter_hours(opens, closes):
        end = start + HOUR
        is_free = start >= now and not any(s < end and e > start for s, e in busy)
        slots.append(HourSlot(start, end, is_free, sum(hour_price(location, start), Decimal("0"))))
    return slots


def get_free_hours_by_day(location: Location, start: date, days: int, *, now: datetime | None = None) -> list[dict]:
    """Число свободных часов зала по дням, одним запросом на весь диапазон."""
    now = now or timezone.now()
    windows = [(day, *working_window(location, day)) for day in (start + timedelta(days=i) for i in range(days))]
    busy = list(
        Booking.objects.filter(
            location_id__in=_location_group_ids(location),
            starts_at__lt=max(closes for _, _, closes in windows),
            ends_at__gt=min(opens for _, opens, _ in windows),
        )
        .filter(_blocking_q(now))
        .values_list("starts_at", "ends_at")
    )
    last_day = _last_bookable_day(now, StudioSettings.load())
    result = []
    for day, opens, closes in windows:
        free = 0
        if day <= last_day:
            day_busy = [(s, e) for s, e in busy if s < closes and e > opens]
            for hour in iter_hours(opens, closes):
                if hour >= now and not any(s < hour + HOUR and e > hour for s, e in day_busy):
                    free += 1
        result.append({"date": day, "free_hours": free})
    return result


def _last_bookable_day(now: datetime, settings: StudioSettings) -> date:
    return timezone.localtime(now).date() + timedelta(days=settings.booking_horizon_days)


def _assert_within_horizon(starts_at: datetime, now: datetime, settings: StudioSettings) -> None:
    if timezone.localtime(starts_at).date() > _last_bookable_day(now, settings):
        raise InvalidTimeRange(f"Бронировать можно не более чем на {settings.booking_horizon_days} дн. вперёд.")


def _resolve_extras(orders: Sequence[ExtraOrder], duration: int, *, lock: bool) -> list[tuple[Extra, int]]:
    ids = [o.extra_id for o in orders]
    if len(set(ids)) != len(ids):
        raise ExtraUnavailable("Одна и та же позиция указана дважды.")
    queryset = Extra.objects.filter(pk__in=ids).order_by("pk")
    if lock:
        queryset = queryset.select_for_update()
    by_id = {e.pk: e for e in queryset}

    resolved = []
    for order in orders:
        extra = by_id.get(order.extra_id)
        if extra is None or not extra.is_active:
            raise ExtraUnavailable("Одна из выбранных позиций недоступна для заказа.")
        hours = duration if order.hours is None else order.hours
        if not 1 <= hours <= duration:
            raise ExtraUnavailable(f"«{extra.title}»: укажите от 1 до {duration} ч.")
        resolved.append((extra, hours))
    return resolved


def _assert_slot_free(group_ids: Iterable[int], starts_at: datetime, ends_at: datetime, now: datetime) -> None:
    taken = (
        Booking.objects.filter(location_id__in=list(group_ids), starts_at__lt=ends_at, ends_at__gt=starts_at)
        .filter(_blocking_q(now))
        .exists()
    )
    if taken:
        raise SlotUnavailable()


def _assert_extras_in_stock(extras: Sequence[tuple[Extra, int]], starts_at: datetime, ends_at: datetime, now: datetime) -> None:
    for extra, _hours in extras:
        if extra.quantity is None:
            continue
        in_use = (
            BookingExtra.objects.filter(extra=extra, booking__starts_at__lt=ends_at, booking__ends_at__gt=starts_at)
            .filter(_blocking_q(now, "booking__"))
            .count()
        )
        if in_use >= extra.quantity:
            raise ExtraUnavailable(f"«{extra.title}» на это время уже занято.")


def _build_quote(
    *,
    user: User | None,
    location: Location,
    starts_at: datetime,
    ends_at: datetime,
    extras: Sequence[ExtraOrder],
    bonus_points: int,
    now: datetime,
    lock: bool,
    allow_past: bool = False,
    settings: StudioSettings | None = None,
) -> BookingQuote:
    if not location.is_active:
        raise LocationUnavailable("Зал сейчас недоступен для бронирования.")
    hours = validate_window(location, starts_at, ends_at, now, allow_past=allow_past)
    if not allow_past:
        _assert_within_horizon(starts_at, now, settings or StudioSettings.load())
    resolved = _resolve_extras(extras, hours, lock=lock)
    _assert_slot_free(_location_group_ids(location), starts_at, ends_at, now)
    _assert_extras_in_stock(resolved, starts_at, ends_at, now)

    price = calculate_price(location, starts_at, ends_at, resolved)

    if bonus_points < 0:
        raise BonusLimitExceeded("Количество баллов не может быть отрицательным.")
    limit = max_spendable_bonus(user, price.subtotal) if user is not None else 0
    if bonus_points > limit:
        raise BonusLimitExceeded(f"Можно списать не более {limit} баллов." if limit else "Оплата баллами для этой брони недоступна.")
    return BookingQuote(
        location=location,
        starts_at=starts_at,
        ends_at=ends_at,
        price=price,
        bonus_points=bonus_points,
        total=price.subtotal - bonus_points,
        max_bonus_points=limit,
    )


def quote_booking(
    *,
    location: Location,
    starts_at: datetime,
    ends_at: datetime,
    extras: Sequence[ExtraOrder] = (),
    user: User | None = None,
    bonus_points: int = 0,
    now: datetime | None = None,
    allow_past: bool = False,
) -> BookingQuote:
    """Проверяет запрос и считает цену без создания брони и без блокировок."""
    return _build_quote(
        user=user,
        location=location,
        starts_at=starts_at,
        ends_at=ends_at,
        extras=extras,
        bonus_points=bonus_points,
        now=now or timezone.now(),
        lock=False,
        allow_past=allow_past,
    )


def create_booking(
    *,
    user: User,
    location: Location,
    starts_at: datetime,
    ends_at: datetime,
    extras: Sequence[ExtraOrder] = (),
    bonus_points: int = 0,
    now: datetime | None = None,
    allow_past: bool = False,
) -> Booking:
    """Создаёт неоплаченную бронь с холдом слота. Если оплачивать нечего (всё покрыто баллами), сразу подтверждает."""
    now = now or timezone.now()
    settings = StudioSettings.load()
    try:
        with transaction.atomic():
            group_ids = _location_group_ids(location)
            list(Location.objects.select_for_update().filter(pk__in=group_ids).order_by("pk"))
            locked_user = User.objects.select_for_update().get(pk=user.pk)

            stale = Booking.objects.filter(location_id__in=group_ids, status=Booking.Status.PENDING, expires_at__lte=now).values_list(
                "pk", flat=True
            )
            _expire_by_ids(list(stale), now)

            quote = _build_quote(
                user=locked_user,
                location=location,
                starts_at=starts_at,
                ends_at=ends_at,
                extras=extras,
                bonus_points=bonus_points,
                now=now,
                lock=True,
                allow_past=allow_past,
                settings=settings,
            )
            booking = Booking.objects.create(
                user=locked_user,
                location=location,
                starts_at=starts_at,
                ends_at=ends_at,
                status=Booking.Status.PENDING,
                expires_at=now + timedelta(minutes=settings.hold_minutes),
                subtotal=quote.price.subtotal,
                bonus_spent=quote.bonus_points,
                total=quote.total,
                price_breakdown=quote.price.breakdown,
            )
            BookingExtra.objects.bulk_create(
                BookingExtra(booking=booking, extra=line.extra, hours=line.hours, unit_price=line.unit_price, amount=line.amount)
                for line in quote.price.extra_lines
            )
            if quote.bonus_points:
                BonusTransaction.objects.create(
                    user=locked_user,
                    booking=booking,
                    kind=BonusTransaction.Kind.SPEND,
                    points=-quote.bonus_points,
                    comment="Оплата брони баллами",
                )
            if quote.total == 0:
                booking = confirm_booking(booking, now=now, settings=settings)
            return booking
    except IntegrityError as exc:
        if OVERLAP_CONSTRAINT in str(exc):
            raise SlotUnavailable() from exc
        raise


def confirm_booking(booking: Booking, *, now: datetime | None = None, settings: StudioSettings | None = None) -> Booking:
    """Подтверждает оплаченную бронь и начисляет кешбэк. Повторный вызов безопасен.

    Для отменённой или просроченной брони бросает InvalidBookingState: деньги в таком случае надо вернуть.
    """
    settings = settings or StudioSettings.load()
    with transaction.atomic():
        locked = Booking.objects.select_for_update().get(pk=booking.pk)
        if locked.status == Booking.Status.CONFIRMED:
            return locked
        if locked.status != Booking.Status.PENDING:
            raise InvalidBookingState("Бронь отменена или не была оплачена вовремя.")

        locked.confirm()
        locked.save(update_fields=["status", "expires_at"])
        _notify_after_commit("send_booking_confirmation", locked.pk)

        points = _cashback_points(locked.total, settings)
        if locked.bonus_spent and not settings.cashback_on_bonus_bookings:
            points = 0
        already = locked.bonus_transactions.filter(kind=BonusTransaction.Kind.ACCRUAL).exists()
        if points and not already:
            BonusTransaction.objects.create(
                user_id=locked.user_id,
                booking=locked,
                kind=BonusTransaction.Kind.ACCRUAL,
                points=points,
                comment=f"Кешбэк {settings.cashback_percent}%",
            )
        return locked


def confirm_booking_manually(booking: Booking, *, now: datetime | None = None) -> Booking:
    """Подтверждает бронь, оплаченную вне сайта (наличные, перевод), и фиксирует ручной платёж."""
    with transaction.atomic():
        confirmed = confirm_booking(booking, now=now)
        has_payment = confirmed.payments.filter(status=Payment.Status.CONFIRMED).exists()
        if confirmed.total and not has_payment:
            Payment.objects.create(
                booking=confirmed,
                provider=Payment.Provider.MANUAL,
                amount=confirmed.total,
                status=Payment.Status.CONFIRMED,
            )
        return confirmed


def cancel_booking(booking: Booking, *, by_staff: bool = False, now: datetime | None = None) -> Booking:
    """Отменяет бронь: сначала решает вопрос с платежом в банке (отмена заказа или возврат), потом освобождает слот.

    Возвращает потраченные баллы и снимает начисленный кешбэк. Если банк недоступен, бронь не меняется
    (PaymentProviderError). Платежи «вручную» возвращаются отдельно — см. `booking.payments`.
    """
    from apps.payments import services as payment_services

    now = now or timezone.now()
    current = Booking.objects.get(pk=booking.pk)
    _assert_cancellable(current, by_staff, now)

    if current.status == Booking.Status.PENDING and payment_services.settle_open_payments(current):
        current = Booking.objects.get(pk=booking.pk)  # оплата успела пройти
        _assert_cancellable(current, by_staff, now)
    if current.status == Booking.Status.CONFIRMED:
        payment_services.refund_booking_payments(current)

    with transaction.atomic():
        locked = Booking.objects.select_for_update().get(pk=booking.pk)
        _assert_cancellable(locked, by_staff, now)
        was_confirmed = locked.status == Booking.Status.CONFIRMED
        locked.cancel(now)
        locked.save(update_fields=["status", "cancelled_at", "expires_at"])

        _refund_spent_bonus(locked)
        _reverse_cashback(locked)
        _close_open_payments(locked)
        if was_confirmed:
            _notify_after_commit("send_booking_cancelled", locked.pk)
        return locked


def _assert_cancellable(booking: Booking, by_staff: bool, now: datetime) -> None:
    if booking.status == Booking.Status.COMPLETED:
        raise InvalidBookingState("Завершённую бронь отменить нельзя.")
    if booking.status in (Booking.Status.CANCELLED, Booking.Status.EXPIRED):
        raise InvalidBookingState("Бронь уже отменена.")
    if booking.status == Booking.Status.CONFIRMED and not by_staff:
        hours = StudioSettings.load().cancel_before_hours
        if now > booking.starts_at - timedelta(hours=hours):
            raise CancellationNotAllowed(f"Отменить бронь можно не позднее чем за {hours} ч до начала. Свяжитесь со студией.")


def expire_pending_bookings(*, now: datetime | None = None) -> int:
    """Снимает холд с неоплаченных броней, у которых истекло время на оплату. Возвращает их количество."""
    now = now or timezone.now()
    ids = Booking.objects.filter(status=Booking.Status.PENDING, expires_at__lte=now).values_list("pk", flat=True)
    return _expire_by_ids(list(ids), now)


def complete_finished_bookings(*, now: datetime | None = None) -> int:
    """Переводит подтверждённые брони, закончившиеся до начала текущего дня (по времени студии), в «Завершена»."""
    now = now or timezone.now()
    day_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    ids = Booking.objects.filter(status=Booking.Status.CONFIRMED, ends_at__lte=day_start).values_list("pk", flat=True)
    completed = 0
    for pk in list(ids):
        with transaction.atomic():
            locked = Booking.objects.select_for_update().get(pk=pk)
            if locked.status != Booking.Status.CONFIRMED:
                continue
            locked.complete()
            locked.save(update_fields=["status"])
            completed += 1
    return completed


def _expire_by_ids(ids: Sequence, now: datetime) -> int:
    from apps.payments import services as payment_services
    from apps.payments.providers import PaymentProviderError

    expired = 0
    for pk in ids:
        booking = Booking.objects.filter(pk=pk, status=Booking.Status.PENDING, expires_at__lte=now).first()
        if booking is None:
            continue
        try:
            payment_services.settle_open_payments(booking)  # HTTP вне блокировки; оплаченная бронь подтвердится
        except PaymentProviderError:
            continue  # банк недоступен: холд снимем при следующей проверке
        with transaction.atomic():
            locked = Booking.objects.select_for_update().get(pk=pk)
            if locked.status != Booking.Status.PENDING or locked.expires_at is None or locked.expires_at > now:
                continue
            locked.expire()
            locked.save(update_fields=["status"])
            _refund_spent_bonus(locked)
            _close_open_payments(locked)
            expired += 1
    return expired


def _refund_spent_bonus(booking: Booking) -> None:
    if not booking.bonus_spent:
        return
    if booking.bonus_transactions.filter(kind=BonusTransaction.Kind.REFUND).exists():
        return
    BonusTransaction.objects.create(
        user_id=booking.user_id,
        booking=booking,
        kind=BonusTransaction.Kind.REFUND,
        points=booking.bonus_spent,
        comment="Возврат баллов за несостоявшуюся бронь",
    )


def _reverse_cashback(booking: Booking) -> None:
    """Снимает кешбэк за бронь, но не больше текущего баланса: баланс не может уйти в минус."""
    kinds = {BonusTransaction.Kind.ACCRUAL, BonusTransaction.Kind.REVERSAL}
    remaining = sum(t.points for t in booking.bonus_transactions.filter(kind__in=kinds))
    if remaining <= 0:
        return
    user = User.objects.select_for_update().get(pk=booking.user_id)
    amount = min(remaining, user.bonus_balance)
    if amount > 0:
        BonusTransaction.objects.create(
            user=user,
            booking=booking,
            kind=BonusTransaction.Kind.REVERSAL,
            points=-amount,
            comment="Отмена брони: кешбэк снят",
        )


def _close_open_payments(booking: Booking) -> None:
    Payment.objects.filter(booking=booking, status__in=[Payment.Status.NEW, Payment.Status.PENDING]).update(status=Payment.Status.EXPIRED)
