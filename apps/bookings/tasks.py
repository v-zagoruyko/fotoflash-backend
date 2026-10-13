from celery import shared_task
from django.utils import timezone

from . import emails, services
from .models import Booking

RETRY = {"autoretry_for": (OSError,), "retry_backoff": 60, "max_retries": 5}


@shared_task
def expire_pending_bookings() -> int:
    return services.expire_pending_bookings()


@shared_task
def complete_finished_bookings() -> int:
    return services.complete_finished_bookings()


@shared_task(**RETRY)
def send_booking_confirmation(booking_id: int) -> None:
    booking = Booking.objects.select_related("user", "location__rules_document").filter(pk=booking_id).first()
    if booking is not None and booking.status == Booking.Status.CONFIRMED:
        emails.send_confirmation(booking)


@shared_task(**RETRY)
def send_booking_cancelled(booking_id: int) -> None:
    booking = Booking.objects.select_related("user", "location").filter(pk=booking_id).first()
    if booking is not None and booking.status == Booking.Status.CANCELLED:
        emails.send_cancellation(booking)


@shared_task(**RETRY)
def send_booking_reminder(booking_id: int) -> None:
    now = timezone.now()
    claimed = Booking.objects.filter(pk=booking_id, status=Booking.Status.CONFIRMED, reminder_sent_at__isnull=True).update(
        reminder_sent_at=now
    )
    if not claimed:
        return
    booking = Booking.objects.select_related("user", "location").get(pk=booking_id)
    try:
        emails.send_reminder(booking, now=now)
    except Exception:
        Booking.objects.filter(pk=booking_id).update(reminder_sent_at=None)  # дать повторить отправку
        raise


@shared_task
def send_due_reminders() -> int:
    ids = emails.due_reminder_ids(timezone.now())
    for booking_id in ids:
        send_booking_reminder.delay(booking_id)
    return len(ids)
