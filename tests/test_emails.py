from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.core.files.base import ContentFile

from apps.bookings import emails, services, tasks
from apps.bookings.models import Booking
from apps.catalog.models import Extra
from apps.core.models import StudioSettings
from apps.documents.models import Document

TZ = ZoneInfo("Asia/Yekaterinburg")
NOW = datetime(2030, 1, 1, 12, tzinfo=TZ)


def at(day, hour):
    return datetime(2030, 1, day, tzinfo=TZ) + timedelta(hours=hour)


@pytest.fixture
def with_rules(location, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    document = Document(slug="rules", title="Правила студии")
    document.file.save("rules.pdf", ContentFile(b"%PDF-1.4 test"), save=False)
    document.save()
    location.rules_document = document
    location.save()
    return document


def make_booking(client_user, location, day=7, start=10, end=12, **kwargs):
    return services.create_booking(user=client_user, location=location, starts_at=at(day, start), ends_at=at(day, end), now=NOW, **kwargs)


def test_confirmation_is_sent_after_commit_with_rules_attached(client_user, location, with_rules, django_capture_on_commit_callbacks):
    booking = make_booking(client_user, location)
    with django_capture_on_commit_callbacks(execute=True):
        services.confirm_booking(booking)
        services.confirm_booking(booking)  # повтор не должен слать второе письмо

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["client@example.com"]
    assert "Бронь №" in message.subject and "Лофт" in message.subject
    assert "7 января" in message.subject
    assert "10:00 – 12:00" in message.body
    assert "начислено 90 баллов" in message.body
    assert message.alternatives[0][1] == "text/html"
    assert [(a[0], a[2]) for a in message.attachments] == [("Правила студии.pdf", "application/pdf")]


def test_confirmation_lists_extras_and_contacts(client_user, location, django_capture_on_commit_callbacks):
    StudioSettings.objects.update_or_create(pk=1, defaults={"address": "ул. Ленина, 1", "contact_phone": "+7 343 000-00-00"})
    projector = Extra.objects.create(title="Проектор", hour_price=200)
    booking = make_booking(client_user, location, extras=[services.ExtraOrder(projector.pk, 1)])
    with django_capture_on_commit_callbacks(execute=True):
        services.confirm_booking(booking)

    body = mail.outbox[0].body
    assert "Проектор, 1 ч" in body
    assert "ул. Ленина, 1" in body and "+7 343 000-00-00" in body
    assert mail.outbox[0].attachments == []  # у зала нет файла правил


def test_no_email_without_commit_or_for_unpaid_cancellation(client_user, location, django_capture_on_commit_callbacks):
    booking = make_booking(client_user, location)
    with django_capture_on_commit_callbacks(execute=True):
        services.cancel_booking(booking, now=NOW)  # неоплаченная бронь: письмо не нужно
    assert mail.outbox == []


def test_cancellation_email_mentions_points_and_refund(client_user, location, django_capture_on_commit_callbacks):
    from apps.bonuses.models import BonusTransaction

    BonusTransaction.objects.create(user=client_user, kind=BonusTransaction.Kind.ADJUST, points=500)
    booking = make_booking(client_user, location, bonus_points=500)
    with django_capture_on_commit_callbacks(execute=True):
        services.confirm_booking(booking)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        services.cancel_booking(booking, now=NOW)

    assert len(mail.outbox) == 1
    assert "отменена" in mail.outbox[0].subject
    assert "возвращены на ваш счёт" in mail.outbox[0].body and "500" in mail.outbox[0].body


def test_reminder_selection_window(client_user, location):
    booking = make_booking(client_user, location, day=7)  # создана 1 января
    services.confirm_booking(booking)

    assert emails.due_reminder_ids(at(5, 9)) == []  # ещё больше 24 ч
    assert emails.due_reminder_ids(at(6, 9)) == []  # 25 ч до начала: окно ещё не открылось
    assert emails.due_reminder_ids(at(6, 11)) == [booking.pk]  # 23 ч до начала
    assert emails.due_reminder_ids(at(7, 10)) == []  # уже началась


def test_reminder_is_sent_once(client_user, location):
    booking = make_booking(client_user, location, day=7)
    services.confirm_booking(booking)
    mail.outbox.clear()

    tasks.send_booking_reminder(booking.pk)
    tasks.send_booking_reminder(booking.pk)

    booking.refresh_from_db()
    assert len(mail.outbox) == 1
    assert booking.reminder_sent_at is not None
    assert "Напоминание" in mail.outbox[0].subject
    assert emails.due_reminder_ids(at(6, 11)) == []


def test_booking_made_inside_reminder_window_gets_no_reminder(client_user, location):
    booking = services.create_booking(
        user=client_user, location=location, starts_at=at(1, 20), ends_at=at(1, 22), now=NOW
    )  # через 8 часов после оформления
    Booking.objects.filter(pk=booking.pk).update(created_at=NOW)  # created_at пишется реальным временем
    services.confirm_booking(booking)
    assert emails.due_reminder_ids(at(1, 13)) == []


def test_reminders_can_be_disabled(client_user, location):
    StudioSettings.objects.update_or_create(pk=1, defaults={"reminder_before_hours": 0})
    booking = make_booking(client_user, location, day=7)
    services.confirm_booking(booking)
    assert emails.due_reminder_ids(at(6, 11)) == []


def test_failed_reminder_can_be_retried(client_user, location, monkeypatch):
    booking = make_booking(client_user, location, day=7)
    services.confirm_booking(booking)

    def boom(*args, **kwargs):
        raise OSError("smtp down")

    monkeypatch.setattr(emails, "send_reminder", boom)
    with pytest.raises(OSError):
        tasks.send_booking_reminder.run(booking.pk)
    assert Booking.objects.get(pk=booking.pk).reminder_sent_at is None
