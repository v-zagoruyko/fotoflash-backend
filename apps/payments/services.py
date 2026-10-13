"""Платежи по броням: создание, обработка статусов банка, отмена и возврат.

Порядок при снятии холда: сначала уточняем статус у банка и отменяем открытый заказ,
и только после успеха бронь освобождается. Если банк недоступен, бронь остаётся как есть и обрабатывается позже.
"""

import logging
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from apps.bookings.exceptions import InvalidBookingState
from apps.bookings.models import Booking

from .models import Payment
from .providers import PaymentProviderError, get_provider
from .providers.tbank import to_kopecks

logger = logging.getLogger(__name__)

OPEN = (Payment.Status.NEW, Payment.Status.PENDING)
MIN_HOLD_LEFT = timedelta(minutes=2)

STATUS_MAP = {
    "NEW": Payment.Status.PENDING,
    "FORM_SHOWED": Payment.Status.PENDING,
    "AUTHORIZING": Payment.Status.PENDING,
    "3DS_CHECKING": Payment.Status.PENDING,
    "3DS_CHECKED": Payment.Status.PENDING,
    "AUTHORIZED": Payment.Status.PENDING,
    "CONFIRMING": Payment.Status.PENDING,
    "PREAUTHORIZING": Payment.Status.PENDING,
    "CONFIRMED": Payment.Status.CONFIRMED,
    "REJECTED": Payment.Status.REJECTED,
    "CANCELED": Payment.Status.EXPIRED,
    "DEADLINE_EXPIRED": Payment.Status.EXPIRED,
    "REVERSING": Payment.Status.EXPIRED,
    "REVERSED": Payment.Status.EXPIRED,
    "REFUNDING": Payment.Status.REFUNDED,
    "ASYNC_REFUNDING": Payment.Status.REFUNDED,
    "REFUNDED": Payment.Status.REFUNDED,
    "PARTIAL_REFUNDED": Payment.Status.REFUNDED,
}


def _provider(payment: Payment):
    return get_provider(payment.provider)


def start_payment(booking: Booking, *, now: datetime | None = None) -> Payment:
    """Возвращает действующую ссылку на оплату или создаёт новый платёж в банке."""
    from django.conf import settings

    now = now or timezone.now()
    with transaction.atomic():
        locked = Booking.objects.select_for_update().select_related("user", "location").get(pk=booking.pk)
        if locked.status != Booking.Status.PENDING:
            raise InvalidBookingState("Бронь не ждёт оплаты.")
        if locked.expires_at is None or locked.expires_at - now < MIN_HOLD_LEFT:
            raise InvalidBookingState("Время на оплату истекло. Оформите бронь заново.")
        if locked.total <= 0:
            raise InvalidBookingState("Эта бронь не требует оплаты.")

        live = locked.payments.filter(status=Payment.Status.PENDING).exclude(payment_url="").first()
        if live is not None:
            return live
        payment = Payment.objects.create(booking=locked, provider=settings.PAYMENT_PROVIDER, amount=locked.total, status=Payment.Status.NEW)

    try:
        result = _provider(payment).init(payment, locked)
    except PaymentProviderError as exc:
        payment.status = Payment.Status.REJECTED
        payment.raw = {"error": exc.details}
        payment.save(update_fields=["status", "raw", "updated_at"])
        raise

    payment.provider_payment_id = result.payment_id
    payment.payment_url = result.url
    payment.status = STATUS_MAP.get(result.status, Payment.Status.PENDING)
    payment.raw = result.raw
    payment.save(update_fields=["provider_payment_id", "payment_url", "status", "raw", "updated_at"])
    return payment


def apply_status(payment: Payment, provider_status: str, raw: dict | None = None) -> Payment:
    """Применяет статус банка к платежу (идемпотентно) и подтверждает бронь при оплате."""
    new_status = STATUS_MAP.get(provider_status)
    if new_status is None:
        logger.warning("Unknown payment status %r for payment %s", provider_status, payment.pk)
        return payment

    from apps.bookings import services as booking_services

    needs_refund = False
    with transaction.atomic():
        locked = Payment.objects.select_for_update().select_related("booking").get(pk=payment.pk)
        regress = locked.status == Payment.Status.CONFIRMED and new_status != Payment.Status.REFUNDED
        if regress or locked.status == Payment.Status.REFUNDED:
            return locked
        locked.status = new_status
        if raw is not None:
            locked.raw = {**locked.raw, "last_notification": raw}
        locked.save(update_fields=["status", "raw", "updated_at"])

        if new_status == Payment.Status.CONFIRMED:
            try:
                booking_services.confirm_booking(locked.booking)
            except InvalidBookingState:
                needs_refund = True  # деньги пришли за бронь, которая уже отменена или просрочена
    if needs_refund:
        logger.warning("Payment %s confirmed for inactive booking %s: refunding", locked.pk, locked.booking_id)
        refund_payment(locked)
    return locked


def settle_open_payments(booking: Booking) -> bool:
    """Перед снятием холда: уточняет статус у банка и отменяет открытые заказы.

    Возвращает True, если бронь на самом деле уже оплачена (подтверждается). Если банк недоступен, бросает PaymentProviderError.
    """
    for payment in booking.payments.filter(status__in=OPEN):
        if payment.provider == Payment.Provider.MANUAL:
            continue
        if not payment.provider_payment_id:
            Payment.objects.filter(pk=payment.pk).update(status=Payment.Status.EXPIRED)
            continue
        provider = _provider(payment)
        status = STATUS_MAP.get(provider.state(payment))
        if status == Payment.Status.CONFIRMED:
            apply_status(payment, "CONFIRMED")
            return True
        if status in (Payment.Status.REJECTED, Payment.Status.EXPIRED, Payment.Status.REFUNDED):
            apply_status(payment, provider.state(payment))
            continue
        provider.cancel(payment)
        apply_status(payment, "CANCELED")
    return False


def refund_payment(payment: Payment) -> bool:
    """Возврат оплаченного платежа в банке. False, если возврат нужно сделать вручную или он не удался."""
    if payment.provider == Payment.Provider.MANUAL:
        return False
    try:
        status = _provider(payment).cancel(payment)
    except PaymentProviderError:
        logger.exception("Refund failed for payment %s", payment.pk)
        return False
    apply_status(payment, status if STATUS_MAP.get(status) == Payment.Status.REFUNDED else "REFUNDED")
    return True


def refund_booking_payments(booking: Booking) -> bool:
    """Возвращает деньги по оплаченной брони. Бросает PaymentProviderError, если банк отказал.

    Возвращает True, если остались платежи, которые нужно вернуть вручную.
    """
    manual_left = False
    for payment in booking.payments.filter(status=Payment.Status.CONFIRMED):
        if payment.provider == Payment.Provider.MANUAL:
            manual_left = True
            continue
        status = _provider(payment).cancel(payment)
        apply_status(payment, status if STATUS_MAP.get(status) == Payment.Status.REFUNDED else "REFUNDED")
    return manual_left


def handle_notification(payload: dict) -> Payment | None:
    """Обрабатывает уведомление банка. Подпись проверяет вызывающий. Возвращает платёж или None, если он неизвестен."""
    payment = (
        Payment.objects.select_related("booking")
        .filter(provider=Payment.Provider.TBANK, provider_payment_id=str(payload.get("PaymentId", "")))
        .first()
    )
    if payment is None:
        logger.warning("Notification for unknown payment %r", payload.get("PaymentId"))
        return None
    if int(payload.get("Amount", -1)) != to_kopecks(payment.amount):
        logger.error("Amount mismatch for payment %s: %r", payment.pk, payload.get("Amount"))
        return payment
    return apply_status(payment, str(payload.get("Status", "")), raw=dict(payload))


def reconcile_payments(*, now: datetime | None = None) -> dict:
    """Подстраховка на случай потерянных уведомлений: сверяет открытые платежи и возвращает «осиротевшие» оплаты."""
    now = now or timezone.now()
    checked = refunded = 0
    open_payments = Payment.objects.filter(status=Payment.Status.PENDING, created_at__lte=now - timedelta(minutes=2))
    for payment in open_payments.exclude(provider=Payment.Provider.MANUAL).exclude(provider_payment_id=""):
        try:
            apply_status(payment, _provider(payment).state(payment))
            checked += 1
        except PaymentProviderError:
            logger.warning("State check failed for payment %s", payment.pk)

    orphaned = Payment.objects.filter(
        status=Payment.Status.CONFIRMED,
        booking__status__in=[Booking.Status.CANCELLED, Booking.Status.EXPIRED],
    ).exclude(provider=Payment.Provider.MANUAL)
    for payment in orphaned:
        refunded += refund_payment(payment)
    return {"checked": checked, "refunded": refunded}
