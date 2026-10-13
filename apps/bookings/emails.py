"""Письма клиенту по брони: подтверждение (с PDF правил зала), напоминание, отмена."""

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.formats import date_format

from apps.bonuses.models import BonusTransaction
from apps.core.models import StudioSettings
from apps.payments.models import Payment

from .models import Booking

logger = logging.getLogger(__name__)


def _rules_attachment(booking: Booking) -> tuple[str, bytes] | None:
    document = booking.location.rules_document
    if document is None or not document.file:
        return None
    try:
        with document.file.open("rb") as handle:
            return f"{document.title}.pdf", handle.read()
    except OSError:
        logger.warning("Rules PDF for location %s is unreadable", booking.location_id)
        return None


def _context(booking: Booking, now: datetime) -> dict:
    studio = StudioSettings.load()
    starts, ends = timezone.localtime(booking.starts_at), timezone.localtime(booking.ends_at)
    deadline = booking.starts_at - timedelta(hours=studio.cancel_before_hours)
    cashback = sum(t.points for t in booking.bonus_transactions.filter(kind=BonusTransaction.Kind.ACCRUAL)) + sum(
        t.points for t in booking.bonus_transactions.filter(kind=BonusTransaction.Kind.REVERSAL)
    )
    return {
        "booking": booking,
        "user_name": booking.user.name,
        "starts": starts,
        "ends": ends,
        "hours": int((booking.ends_at - booking.starts_at) / timedelta(hours=1)),
        "extras": list(booking.extras.select_related("extra")),
        "cashback": max(cashback, 0),
        "cancel_deadline": timezone.localtime(deadline) if deadline > now else None,
        "studio": studio,
        "url": f"{settings.FRONTEND_URL}/profile/bookings/{booking.pk}",
    }


def _send(booking: Booking, template: str, subject: str, context: dict, attachment=None) -> None:
    context = {**context, "has_rules": attachment is not None}
    message = EmailMultiAlternatives(
        subject,
        render_to_string(f"bookings/email/{template}.txt", context).strip(),
        to=[booking.user.email],
    )
    message.attach_alternative(render_to_string(f"bookings/email/{template}.html", context), "text/html")
    if attachment:
        message.attach(attachment[0], attachment[1], "application/pdf")
    message.send()


def send_confirmation(booking: Booking, *, now: datetime | None = None) -> None:
    now = now or timezone.now()
    context = _context(booking, now)
    when = date_format(context["starts"], "j E, H:i")
    _send(
        booking,
        "confirmed",
        f"Бронь №{booking.pk} подтверждена: {booking.location.title}, {when}",
        context,
        attachment=_rules_attachment(booking),
    )


def send_reminder(booking: Booking, *, now: datetime | None = None) -> None:
    now = now or timezone.now()
    context = _context(booking, now)
    when = date_format(context["starts"], "j E, H:i")
    _send(booking, "reminder", f"Напоминание: бронь №{booking.pk}, {when}", context)


def send_cancellation(booking: Booking, *, now: datetime | None = None) -> None:
    now = now or timezone.now()
    context = _context(booking, now)
    context["refunded_online"] = booking.payments.filter(status=Payment.Status.REFUNDED).exclude(provider=Payment.Provider.MANUAL).exists()
    context["bonus_returned"] = booking.bonus_spent if booking.bonus_transactions.filter(kind=BonusTransaction.Kind.REFUND).exists() else 0
    _send(booking, "cancelled", f"Бронь №{booking.pk} отменена", context)


def due_reminder_ids(now: datetime) -> list[int]:
    """Подтверждённые брони, до начала которых осталось не больше N часов и напоминание ещё не отправлялось.

    Бронь, оформленную уже внутри этого окна, не напоминаем: подтверждение пришло недавно.
    """
    hours = StudioSettings.load().reminder_before_hours
    if not hours:
        return []
    window = timedelta(hours=hours)
    from django.db.models import F

    return list(
        Booking.objects.filter(
            status=Booking.Status.CONFIRMED,
            reminder_sent_at__isnull=True,
            starts_at__gt=now,
            starts_at__lte=now + window,
            created_at__lte=F("starts_at") - window,
        ).values_list("pk", flat=True)
    )
