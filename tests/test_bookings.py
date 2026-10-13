import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.bookings.models import Booking


def make_booking(user, location, start, end, status=Booking.Status.CONFIRMED):
    return Booking.objects.create(user=user, location=location, starts_at=start, ends_at=end, status=status, subtotal=3000, total=3000)


def test_overlapping_active_bookings_are_rejected(client_user, location, slot):
    start, end = slot
    make_booking(client_user, location, start, end)
    with transaction.atomic(), pytest.raises(IntegrityError):
        make_booking(client_user, location, start + (end - start) / 2, end + (end - start))


def test_adjacent_bookings_are_allowed(client_user, location, slot):
    start, end = slot
    make_booking(client_user, location, start, end)
    make_booking(client_user, location, end, end + (end - start))


def test_cancelled_booking_frees_slot(client_user, location, slot):
    start, end = slot
    make_booking(client_user, location, start, end, status=Booking.Status.CANCELLED)
    make_booking(client_user, location, start, end)


def test_model_validation_reports_busy_slot_in_russian(client_user, location, slot):
    start, end = slot
    make_booking(client_user, location, start, end)
    duplicate = Booking(
        user=client_user,
        location=location,
        starts_at=start,
        ends_at=end,
        status=Booking.Status.PENDING,
        subtotal=3000,
        total=3000,
    )
    try:
        duplicate.validate_constraints()
    except ValidationError as exc:
        assert "В это время зал уже занят." in str(exc)
    else:
        raise AssertionError("ValidationError expected")
