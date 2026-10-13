from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.bonuses.models import BonusTransaction
from apps.bookings import services
from apps.bookings.exceptions import (
    BonusLimitExceeded,
    CancellationNotAllowed,
    ExtraUnavailable,
    InvalidBookingState,
    InvalidTimeRange,
    SlotUnavailable,
)
from apps.bookings.models import Booking
from apps.bookings.services import ExtraOrder
from apps.catalog.models import Extra, Location
from apps.core.models import StudioSettings
from apps.payments.models import Payment

TZ = ZoneInfo("Asia/Yekaterinburg")
NOW = datetime(2030, 1, 1, 12, tzinfo=TZ)
MONDAY = date(2030, 1, 7)
SATURDAY = date(2030, 1, 5)


def at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=TZ) + timedelta(hours=hour)


def book(user, location, day, start_hour, end_hour, **kwargs):
    kwargs.setdefault("now", NOW)
    return services.create_booking(user=user, location=location, starts_at=at(day, start_hour), ends_at=at(day, end_hour), **kwargs)


def give_points(user, points):
    BonusTransaction.objects.create(user=user, kind=BonusTransaction.Kind.ADJUST, points=points)
    user.refresh_from_db()


@pytest.fixture
def projector(db):
    return Extra.objects.create(title="Проектор", hour_price=200)


@pytest.fixture
def second_location(db):
    return Location.objects.create(title="Студия 2", slug="studio-2", hour_price=1000, cover="y.jpg")


# --- цена -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "start", "end", "expected"),
    [
        (MONDAY, 10, 12, "3000"),  # будни, обычное время
        (MONDAY, 21, 23, "3300"),  # 22:00-23:00 — поздний тариф
        (SATURDAY, 10, 12, "4000"),  # выходной: +500 за час
        (SATURDAY, 22, 24, "4600"),  # выходной и поздний, бронь до полуночи
    ],
)
def test_price_per_hour_rules(client_user, location, day, start, end, expected):
    quote = services.quote_booking(location=location, starts_at=at(day, start), ends_at=at(day, end), now=NOW)
    assert quote.price.subtotal == Decimal(expected)
    assert quote.total == Decimal(expected)


def test_price_breakdown_is_saved_on_booking(client_user, location):
    booking = book(client_user, location, SATURDAY, 21, 23)
    assert booking.price_breakdown["hours"] == 2
    assert booking.price_breakdown["late"] == "300"
    assert booking.price_breakdown["weekend"] == "1000"
    assert booking.subtotal == Decimal("4300")


def test_extras_are_priced_per_hour_and_snapshotted(client_user, location, projector):
    booking = book(client_user, location, MONDAY, 10, 13, extras=[ExtraOrder(projector.pk)])
    line = booking.extras.get()
    assert (line.hours, line.unit_price, line.amount) == (3, Decimal("200"), Decimal("600"))
    assert booking.subtotal == Decimal("4500") + Decimal("600")

    projector.hour_price = 999
    projector.save()
    assert booking.extras.get().unit_price == Decimal("200")


def test_extra_can_be_ordered_for_part_of_booking(client_user, location, projector):
    booking = book(client_user, location, MONDAY, 10, 13, extras=[ExtraOrder(projector.pk, hours=1)])
    assert booking.extras.get().amount == Decimal("200")


# --- валидация времени ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (at(MONDAY, 10) + timedelta(minutes=30), at(MONDAY, 12)),  # не целый час
        (at(MONDAY, 12), at(MONDAY, 10)),  # конец раньше начала
        (at(MONDAY, 8), at(MONDAY, 10)),  # до открытия
        (at(MONDAY, 23), at(MONDAY, 25)),  # после закрытия
    ],
)
def test_invalid_time_ranges_are_rejected(client_user, location, start, end):
    with pytest.raises(InvalidTimeRange):
        services.quote_booking(location=location, starts_at=start, ends_at=end, now=NOW)


def test_past_and_naive_times_are_rejected(client_user, location):
    with pytest.raises(InvalidTimeRange):
        services.quote_booking(location=location, starts_at=at(MONDAY, 10), ends_at=at(MONDAY, 12), now=at(MONDAY, 11))
    with pytest.raises(InvalidTimeRange):
        services.quote_booking(location=location, starts_at=datetime(2030, 1, 7, 10), ends_at=datetime(2030, 1, 7, 12), now=NOW)


def test_min_hours_is_enforced(client_user, location):
    location.min_hours = 2
    location.save()
    with pytest.raises(InvalidTimeRange, match="Минимальная длительность"):
        book(client_user, location, MONDAY, 10, 11)


# --- слоты и холд ---------------------------------------------------------------------------------


def test_busy_slot_cannot_be_booked_but_adjacent_can(client_user, location):
    book(client_user, location, MONDAY, 10, 12)
    with pytest.raises(SlotUnavailable):
        book(client_user, location, MONDAY, 11, 13)
    book(client_user, location, MONDAY, 12, 14)


def test_conflicting_location_blocks_slot(client_user, location, second_location):
    location.conflicts_with.add(second_location)
    book(client_user, location, MONDAY, 10, 12)
    with pytest.raises(SlotUnavailable):
        book(client_user, second_location, MONDAY, 10, 12)
    book(client_user, second_location, MONDAY, 12, 14)


def test_unrelated_locations_do_not_block_each_other(client_user, location, second_location):
    book(client_user, location, MONDAY, 10, 12)
    book(client_user, second_location, MONDAY, 10, 12)


def test_unpaid_booking_holds_slot_then_releases_it(client_user, location):
    first = book(client_user, location, MONDAY, 10, 12)
    assert first.expires_at == NOW + timedelta(minutes=30)

    later = NOW + timedelta(minutes=31)
    second = book(client_user, location, MONDAY, 10, 12, now=later)

    first.refresh_from_db()
    assert first.status == Booking.Status.EXPIRED
    assert second.status == Booking.Status.PENDING


def test_expire_sweep_releases_holds_and_refunds_points(client_user, location):
    give_points(client_user, 1000)
    booking = book(client_user, location, MONDAY, 10, 12, bonus_points=500)
    client_user.refresh_from_db()
    assert client_user.bonus_balance == 500

    assert services.expire_pending_bookings(now=NOW + timedelta(minutes=10)) == 0
    assert services.expire_pending_bookings(now=NOW + timedelta(minutes=31)) == 1
    assert services.expire_pending_bookings(now=NOW + timedelta(minutes=32)) == 0

    booking.refresh_from_db()
    client_user.refresh_from_db()
    assert booking.status == Booking.Status.EXPIRED
    assert client_user.bonus_balance == 1000


def test_day_slots_show_availability_and_prices(client_user, location):
    book(client_user, location, MONDAY, 10, 12)
    slots = {s.starts_at.hour: s for s in services.get_day_slots(location, MONDAY, now=NOW)}
    assert not slots[10].is_available
    assert not slots[11].is_available
    assert slots[12].is_available
    assert slots[10].price == Decimal("1500")
    assert slots[22].price == Decimal("1800")
    assert len(slots) == 15  # 09:00-00:00


def test_past_hours_are_not_available(location):
    slots = services.get_day_slots(location, MONDAY, now=at(MONDAY, 12))
    assert [s.is_available for s in slots if s.starts_at.hour in (11, 12)] == [False, True]


# --- допы -----------------------------------------------------------------------------------------


def test_extra_stock_is_limited_by_quantity(client_user, location, second_location, projector):
    projector.quantity = 1
    projector.save()
    book(client_user, location, MONDAY, 10, 12, extras=[ExtraOrder(projector.pk)])
    with pytest.raises(ExtraUnavailable):
        book(client_user, second_location, MONDAY, 11, 13, extras=[ExtraOrder(projector.pk)])
    book(client_user, second_location, MONDAY, 12, 14, extras=[ExtraOrder(projector.pk)])


def test_inactive_and_duplicate_extras_are_rejected(client_user, location, projector):
    with pytest.raises(ExtraUnavailable):
        book(client_user, location, MONDAY, 10, 12, extras=[ExtraOrder(projector.pk), ExtraOrder(projector.pk)])
    projector.is_active = False
    projector.save()
    with pytest.raises(ExtraUnavailable):
        book(client_user, location, MONDAY, 10, 12, extras=[ExtraOrder(projector.pk)])


# --- баллы, подтверждение, отмена -----------------------------------------------------------------


def test_bonus_is_limited_by_balance_and_share(client_user, location):
    give_points(client_user, 1000)
    with pytest.raises(BonusLimitExceeded):
        book(client_user, location, MONDAY, 10, 12, bonus_points=1001)

    give_points(client_user, 5000)  # баланс 6000, но не больше 50% от 3000
    quote = services.quote_booking(location=location, starts_at=at(MONDAY, 10), ends_at=at(MONDAY, 12), user=client_user, now=NOW)
    assert quote.max_bonus_points == 1500
    with pytest.raises(BonusLimitExceeded):
        book(client_user, location, MONDAY, 10, 12, bonus_points=1501)


def test_booking_with_points_reduces_total_and_spends_balance(client_user, location):
    give_points(client_user, 1000)
    booking = book(client_user, location, MONDAY, 10, 12, bonus_points=700)
    client_user.refresh_from_db()
    assert (booking.subtotal, booking.bonus_spent, booking.total) == (Decimal("3000"), 700, Decimal("2300"))
    assert client_user.bonus_balance == 300


def test_confirm_accrues_cashback_once(client_user, location):
    booking = book(client_user, location, MONDAY, 10, 12)
    services.confirm_booking(booking)
    services.confirm_booking(booking)

    booking.refresh_from_db()
    client_user.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.expires_at is None
    assert client_user.bonus_balance == 90  # 3% от 3000
    assert booking.bonus_transactions.filter(kind=BonusTransaction.Kind.ACCRUAL).count() == 1


def test_cashback_is_calculated_from_cash_part(client_user, location):
    give_points(client_user, 1000)
    booking = book(client_user, location, MONDAY, 10, 12, bonus_points=1000)
    services.confirm_booking(booking)
    client_user.refresh_from_db()
    assert client_user.bonus_balance == 60  # 3% от 2000


def test_confirming_expired_booking_is_an_error(client_user, location):
    booking = book(client_user, location, MONDAY, 10, 12)
    services.expire_pending_bookings(now=NOW + timedelta(hours=1))
    with pytest.raises(InvalidBookingState):
        services.confirm_booking(booking)


def test_fully_paid_with_points_is_confirmed_immediately(client_user, location):
    StudioSettings.objects.update_or_create(pk=1, defaults={"max_bonus_share_percent": 100})
    give_points(client_user, 3000)
    booking = book(client_user, location, MONDAY, 10, 12, bonus_points=3000)
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.total == 0
    client_user.refresh_from_db()
    assert client_user.bonus_balance == 0


def test_client_cannot_cancel_late_but_staff_can(client_user, location):
    booking = book(client_user, location, MONDAY, 10, 12)
    services.confirm_booking(booking)

    too_late = at(MONDAY, 10) - timedelta(hours=23)
    with pytest.raises(CancellationNotAllowed):
        services.cancel_booking(booking, now=too_late)

    services.cancel_booking(booking, by_staff=True, now=too_late)
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
    assert booking.cancelled_at == too_late


def test_client_can_cancel_in_time_and_points_are_restored(client_user, location):
    give_points(client_user, 1000)
    booking = book(client_user, location, MONDAY, 10, 12, bonus_points=500)
    services.confirm_booking(booking)  # кешбэк 3% от 2500 = 75, баланс 575

    services.cancel_booking(booking, now=NOW)

    client_user.refresh_from_db()
    assert client_user.bonus_balance == 1000  # 500 возвращены, 75 кешбэка сняты
    assert client_user.bonus_balance == 1000
    with pytest.raises(InvalidBookingState):
        services.cancel_booking(booking, now=NOW)


def test_cashback_reversal_never_makes_balance_negative(client_user, location):
    booking = book(client_user, location, MONDAY, 10, 12)
    services.confirm_booking(booking)  # +90
    BonusTransaction.objects.create(user=client_user, kind=BonusTransaction.Kind.ADJUST, points=-50)

    services.cancel_booking(booking, by_staff=True, now=NOW)

    client_user.refresh_from_db()
    assert client_user.bonus_balance == 0


def test_cancelled_booking_frees_slot_and_closes_open_payment(client_user, location):
    booking = book(client_user, location, MONDAY, 10, 12)
    payment = Payment.objects.create(booking=booking, amount=booking.total, status=Payment.Status.PENDING)

    services.cancel_booking(booking, now=NOW)

    payment.refresh_from_db()
    assert payment.status == Payment.Status.EXPIRED
    book(client_user, location, MONDAY, 10, 12)


# --- админка и фоновые задачи ---------------------------------------------------------------------


def test_admin_actions_confirm_manually_and_cancel(client, client_user, location):
    from django.contrib.auth import get_user_model
    from django.urls import reverse

    staff = get_user_model().objects.create_superuser("admin@example.com", "pass12345")
    client.force_login(staff)
    booking = book(client_user, location, MONDAY, 10, 12)
    url = reverse("admin:bookings_booking_changelist")

    client.post(url, {"action": "confirm_paid_manually", "_selected_action": [str(booking.pk)]})
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    payment = booking.payments.get()
    assert (payment.provider, payment.status, payment.amount) == (Payment.Provider.MANUAL, Payment.Status.CONFIRMED, Decimal("3000"))

    client.post(url, {"action": "cancel_selected", "_selected_action": [str(booking.pk)]})
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED


def test_expire_task_is_registered_in_beat_schedule():
    from django.conf import settings

    from apps.bookings.tasks import expire_pending_bookings

    entry = settings.CELERY_BEAT_SCHEDULE["expire-pending-bookings"]
    assert entry["task"] == expire_pending_bookings.name


def test_cashback_flag_disables_cashback_for_bookings_paid_with_points(client_user, location):
    StudioSettings.objects.update_or_create(pk=1, defaults={"cashback_on_bonus_bookings": False})
    give_points(client_user, 1000)
    with_points = book(client_user, location, MONDAY, 10, 12, bonus_points=500)
    without_points = book(client_user, location, MONDAY, 12, 14)
    services.confirm_booking(with_points)
    services.confirm_booking(without_points)

    client_user.refresh_from_db()
    assert client_user.bonus_balance == 500 + 90  # за бронь с баллами кешбэка нет, за вторую 3% от 3000


def test_manual_confirmation_creates_single_manual_payment(client_user, location):
    booking = book(client_user, location, MONDAY, 10, 12)
    services.confirm_booking_manually(booking)
    services.confirm_booking_manually(booking)
    payment = booking.payments.get()
    assert (payment.provider, payment.amount) == (Payment.Provider.MANUAL, Decimal("3000"))


# --- ручная бронь в админке -----------------------------------------------------------------------


@pytest.fixture
def staff_client(client, db):
    from django.contrib.auth import get_user_model

    client.force_login(get_user_model().objects.create_superuser("admin@example.com", "pass12345"))
    return client


def add_booking_payload(user, location, start="10:00:00", end="12:00:00", **extra):
    return {
        "user": user.pk,
        "location": location.pk,
        "starts_at_0": "2030-01-07",
        "starts_at_1": start,
        "ends_at_0": "2030-01-07",
        "ends_at_1": end,
        **extra,
    }


def test_admin_add_goes_through_services(staff_client, client_user, location, projector):
    from django.urls import reverse

    give_points(client_user, 1000)
    response = staff_client.post(
        reverse("admin:bookings_booking_add"),
        add_booking_payload(client_user, location, extras=[projector.pk], bonus_points=500),
    )
    assert response.status_code == 302, response.content.decode()[:2000]

    booking = Booking.objects.get()
    assert booking.status == Booking.Status.PENDING
    assert booking.expires_at is not None
    assert booking.subtotal == Decimal("3000") + Decimal("400")
    assert (booking.bonus_spent, booking.total) == (500, Decimal("2900"))
    assert booking.extras.get().extra == projector
    client_user.refresh_from_db()
    assert client_user.bonus_balance == 500


def test_admin_add_paid_manually_confirms_booking(staff_client, client_user, location):
    from django.urls import reverse

    staff_client.post(reverse("admin:bookings_booking_add"), add_booking_payload(client_user, location, paid_manually="on"))
    booking = Booking.objects.get()
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.payments.get().provider == Payment.Provider.MANUAL


def test_admin_add_reports_busy_slot_in_form(staff_client, client_user, location):
    from django.urls import reverse

    services.create_booking(user=client_user, location=location, starts_at=at(MONDAY, 10), ends_at=at(MONDAY, 12), now=NOW)
    response = staff_client.post(reverse("admin:bookings_booking_add"), add_booking_payload(client_user, location))
    assert response.status_code == 200
    assert "Это время уже занято" in response.content.decode()
    assert Booking.objects.count() == 1


def test_admin_edit_bypasses_services(staff_client, client_user, location):
    from django.urls import reverse

    booking = book(client_user, location, MONDAY, 10, 12)
    response = staff_client.get(reverse("admin:bookings_booking_change", args=[booking.pk]))
    assert response.status_code == 200
    assert "extras" not in response.context["adminform"].form.fields  # поля ручного создания только при добавлении


# --- FSM, завершение, админка ---------------------------------------------------------------------


def test_status_cannot_be_assigned_directly(client_user, location):
    booking = book(client_user, location, MONDAY, 10, 12)
    with pytest.raises(AttributeError):
        booking.status = Booking.Status.CONFIRMED


def test_invalid_transitions_are_rejected_by_services(client_user, location):
    booking = book(client_user, location, MONDAY, 10, 12)
    services.confirm_booking(booking)
    services.complete_finished_bookings(now=at(MONDAY, 12) + timedelta(days=1))
    with pytest.raises(InvalidBookingState, match="Завершённую"):
        services.cancel_booking(booking, by_staff=True, now=NOW)
    with pytest.raises(InvalidBookingState):
        services.confirm_booking(booking)


def test_bookings_are_completed_at_start_of_next_day(client_user, location):
    evening = book(client_user, location, MONDAY, 20, 24)  # заканчивается ровно в полночь
    morning = book(client_user, location, MONDAY, 10, 12)
    pending = book(client_user, location, MONDAY, 13, 14)
    services.confirm_booking(evening)
    services.confirm_booking(morning)

    assert services.complete_finished_bookings(now=at(MONDAY, 23)) == 0  # ещё идёт тот же день

    assert services.complete_finished_bookings(now=at(MONDAY, 24) + timedelta(minutes=5)) == 2
    for booking in (evening, morning):
        booking.refresh_from_db()
        assert booking.status == Booking.Status.COMPLETED
    pending.refresh_from_db()
    assert pending.status == Booking.Status.PENDING  # неоплаченные не завершаем
    assert services.complete_finished_bookings(now=at(MONDAY, 24) + timedelta(hours=1)) == 0


def test_completed_booking_still_blocks_slot(client_user, location):
    booking = book(client_user, location, MONDAY, 10, 12)
    services.confirm_booking(booking)
    services.complete_finished_bookings(now=at(MONDAY, 12) + timedelta(days=1))
    with pytest.raises(SlotUnavailable):
        services.create_booking(
            user=client_user,
            location=location,
            starts_at=at(MONDAY, 10),
            ends_at=at(MONDAY, 12),
            now=NOW,
            allow_past=True,
        )


def booking_change_payload(booking, **overrides):
    local = booking.starts_at.astimezone(TZ)
    data = {
        "location": booking.location_id,
        "starts_at_0": local.strftime("%Y-%m-%d"),
        "starts_at_1": local.strftime("%H:%M:%S"),
        "ends_at_0": local.strftime("%Y-%m-%d"),
        "ends_at_1": (local + timedelta(hours=2)).strftime("%H:%M:%S"),
        "comment": "",
        "subtotal": "3000",
        "bonus_spent": "0",
        "total": "3000",
    }
    for prefix in ("extras", "payments", "bonus_transactions"):
        data |= {
            f"{prefix}-TOTAL_FORMS": "0",
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
    return data | overrides


def test_admin_booking_change_page_layout(staff_client, client_user, location):
    from django.urls import reverse

    booking = book(client_user, location, MONDAY, 10, 12)
    response = staff_client.get(reverse("admin:bookings_booking_change", args=[booking.pk]))
    assert response.status_code == 200
    form = response.context["adminform"]
    assert "user" in form.readonly_fields and "status" in form.readonly_fields
    assert form.fieldsets[0][1]["fields"][0] == "status"  # статус первым в блоке «Бронь»
    flat = [f for _, opts in form.fieldsets for f in opts["fields"]]
    assert "price_breakdown_display" in flat
    assert "Сохранить и подтвердить оплату" in response.content.decode()


def test_admin_changelist_shows_number_and_searches_by_it(staff_client, client_user, location):
    from django.urls import reverse

    first = book(client_user, location, MONDAY, 10, 12)
    second = book(client_user, location, MONDAY, 14, 16)
    url = reverse("admin:bookings_booking_changelist")

    page = staff_client.get(url).content.decode()
    assert f"№{first.pk}" in page and f"№{second.pk}" in page

    found = staff_client.get(url, {"q": f"№{second.pk}"}).context["cl"].result_list
    assert [b.pk for b in found] == [second.pk]


def test_admin_cannot_delete_bookings(staff_client, client_user, location):
    from django.urls import reverse

    booking = book(client_user, location, MONDAY, 10, 12)
    response = staff_client.post(reverse("admin:bookings_booking_delete", args=[booking.pk]), {"post": "yes"})
    assert response.status_code == 403
    assert Booking.objects.filter(pk=booking.pk).exists()

    url = reverse("admin:bookings_booking_changelist")
    staff_client.post(url, {"action": "delete_selected", "_selected_action": [str(booking.pk)], "post": "yes"})
    assert Booking.objects.filter(pk=booking.pk).exists()


def test_admin_completed_booking_is_read_only(staff_client, client_user, location):
    from django.urls import reverse

    booking = book(client_user, location, MONDAY, 10, 12)
    services.confirm_booking(booking)
    services.complete_finished_bookings(now=at(MONDAY, 12) + timedelta(days=1))
    url = reverse("admin:bookings_booking_change", args=[booking.pk])

    assert staff_client.get(url).status_code == 200
    response = staff_client.post(url, booking_change_payload(booking, comment="Правка"))
    assert response.status_code == 403
    booking.refresh_from_db()
    assert booking.comment == ""


def test_admin_edit_saves_values_as_is(staff_client, client_user, location):
    from django.urls import reverse

    booking = book(client_user, location, MONDAY, 10, 12)
    url = reverse("admin:bookings_booking_change", args=[booking.pk])
    response = staff_client.post(url, booking_change_payload(booking, comment="Звонил, перенесли", total="2500"))
    assert response.status_code == 302, response.content.decode()[:1500]
    booking.refresh_from_db()
    assert (booking.comment, booking.total, booking.status) == ("Звонил, перенесли", Decimal("2500"), Booking.Status.PENDING)


def test_admin_buttons_confirm_and_cancel(staff_client, client_user, location):
    from django.urls import reverse

    booking = book(client_user, location, MONDAY, 10, 12)
    url = reverse("admin:bookings_booking_change", args=[booking.pk])

    staff_client.post(url, booking_change_payload(booking, _confirm_manually="1"))
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.payments.get().provider == Payment.Provider.MANUAL

    staff_client.post(url, booking_change_payload(booking, _cancel_booking="1"))
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
