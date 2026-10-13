import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from rest_framework.test import APIClient

from apps.bonuses.models import BonusTransaction
from apps.bookings import services
from apps.bookings.models import Booking
from apps.catalog.models import Extra
from apps.documents.models import Document
from apps.users.models import User

TZ = ZoneInfo("Asia/Yekaterinburg")
API = "/api/v1"


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def logged(api, client_user):
    api.force_authenticate(client_user)
    return api


def future_slot(days=30, start=10, end=12):
    base = (datetime.now(TZ) + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "starts_at": (base + timedelta(hours=start)).isoformat(),
        "ends_at": (base + timedelta(hours=end)).isoformat(),
    }


def booking_payload(location, **kwargs):
    return {"location": location.slug, **future_slot(), **kwargs}


# --- вход по коду ---------------------------------------------------------------------------------


def request_code(api, email="new@example.com"):
    response = api.post(f"{API}/auth/code/request/", {"email": email}, format="json")
    return response, (re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1) if mail.outbox else None)


def test_csrf_endpoint_sets_cookie(api):
    response = api.get(f"{API}/auth/csrf/")
    assert response.status_code == 200
    assert "csrftoken" in response.cookies


def test_new_client_logs_in_with_code_and_consent(api, db):
    response, code = request_code(api)
    assert response.status_code == 204
    assert mail.outbox[0].to == ["new@example.com"]

    no_consent = api.post(f"{API}/auth/code/verify/", {"email": "new@example.com", "code": code}, format="json")
    assert no_consent.status_code == 400
    assert no_consent.json()["code"] == "consent_required"
    assert not User.objects.filter(email="new@example.com").exists()

    ok = api.post(
        f"{API}/auth/code/verify/",
        {"email": "NEW@example.com", "code": code, "name": "Анна", "consent": True},
        format="json",
    )
    assert ok.status_code == 200, ok.content
    assert ok.json()["email"] == "new@example.com"
    user = User.objects.get(email="new@example.com")
    assert user.name == "Анна" and user.consent_at is not None

    assert api.get(f"{API}/me/").json()["email"] == "new@example.com"
    api.post(f"{API}/auth/logout/")
    assert api.get(f"{API}/me/").status_code in (401, 403)


def test_code_is_single_use_and_wrong_codes_are_limited(api, db):
    _, code = request_code(api)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        response = api.post(f"{API}/auth/code/verify/", {"email": "new@example.com", "code": wrong, "consent": True}, format="json")
        assert response.status_code == 400 and response.json()["code"] == "invalid_code"
    # после 5 неверных попыток верный код тоже не работает
    final = api.post(f"{API}/auth/code/verify/", {"email": "new@example.com", "code": code, "consent": True}, format="json")
    assert final.status_code == 400


def test_code_can_be_used_only_once(api, db):
    _, code = request_code(api)
    payload = {"email": "new@example.com", "code": code, "consent": True}
    assert api.post(f"{API}/auth/code/verify/", payload, format="json").status_code == 200
    again = api.post(f"{API}/auth/code/verify/", payload, format="json")
    assert again.status_code == 400


def test_resend_cooldown(api, db):
    request_code(api)
    response, _ = request_code(api)
    assert response.status_code == 429
    assert response.json()["code"] == "too_many_requests"


def test_existing_client_without_consent_flag_needs_no_new_name(api, client_user):
    client_user.consent_at = datetime.now(TZ)
    client_user.save()
    _, code = request_code(api, client_user.email)
    response = api.post(f"{API}/auth/code/verify/", {"email": client_user.email, "code": code}, format="json")
    assert response.status_code == 200


def test_inactive_account_cannot_log_in(api, client_user):
    client_user.is_active = False
    client_user.save()
    _, code = request_code(api, client_user.email)
    response = api.post(f"{API}/auth/code/verify/", {"email": client_user.email, "code": code, "consent": True}, format="json")
    assert response.status_code == 403
    assert response.json()["code"] == "account_disabled"


def test_me_patch_and_bonus(logged, client_user):
    BonusTransaction.objects.create(user=client_user, kind=BonusTransaction.Kind.ADJUST, points=150, comment="Подарок")
    response = logged.patch(f"{API}/me/", {"name": "Мария", "phone": "+79991234567", "bonus_balance": 99999}, format="json")
    assert response.status_code == 200
    client_user.refresh_from_db()
    assert (client_user.name, client_user.phone, client_user.bonus_balance) == ("Мария", "+79991234567", 150)

    bonus = logged.get(f"{API}/me/bonus/").json()
    assert bonus["balance"] == 150
    assert bonus["transactions"][0]["points"] == 150


# --- каталог --------------------------------------------------------------------------------------


def test_public_catalog(api, location, db):
    Extra.objects.create(title="Проектор", hour_price=200, quantity=3)
    Extra.objects.create(title="Скрытый", hour_price=1, is_active=False)
    Document.objects.create(slug="offer", title="Оферта", file="documents/offer.pdf")
    Document.objects.create(slug="draft", title="Черновик", file="documents/draft.pdf", is_published=False)

    locations = api.get(f"{API}/locations/").json()
    assert [item["slug"] for item in locations] == ["loft"]
    assert locations[0]["hour_price"] == "1500.00"
    assert api.get(f"{API}/locations/loft/").json()["title"] == "Лофт"

    extras = api.get(f"{API}/extras/").json()
    assert [e["title"] for e in extras] == ["Проектор"]
    assert "quantity" not in extras[0]

    documents = api.get(f"{API}/documents/").json()
    assert [d["slug"] for d in documents] == ["offer"]
    assert documents[0]["url"].startswith("http")
    assert api.get(f"{API}/documents/draft/").status_code == 404


def test_availability_reflects_bookings(api, location, client_user):
    payload = booking_payload(location)
    start = datetime.fromisoformat(payload["starts_at"])
    services.create_booking(
        user=client_user,
        location=location,
        starts_at=start,
        ends_at=datetime.fromisoformat(payload["ends_at"]),
    )
    response = api.get(f"{API}/locations/loft/availability/", {"date": start.date().isoformat(), "days": 2})
    assert response.status_code == 200
    days = response.json()
    assert len(days) == 2
    slots = {datetime.fromisoformat(s["starts_at"]).hour: s for s in days[0]["slots"]}
    assert slots[10]["is_available"] is False and slots[11]["is_available"] is False
    assert slots[12]["is_available"] is True
    assert slots[22]["price"] != slots[12]["price"]  # поздний тариф

    assert api.get(f"{API}/locations/loft/availability/").status_code == 400  # нет date


# --- брони ----------------------------------------------------------------------------------------


def test_quote_works_for_guests_and_needs_login_for_points(api, location):
    response = api.post(f"{API}/bookings/quote/", booking_payload(location), format="json")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["hours"] == 2 and body["max_bonus_points"] == 0
    assert float(body["subtotal"]) >= 3000

    with_points = api.post(f"{API}/bookings/quote/", booking_payload(location, bonus_points=100), format="json")
    assert with_points.status_code == 400
    assert with_points.json()["code"] == "bonus_limit_exceeded"


def test_create_requires_login(api, location, db):
    response = api.post(f"{API}/bookings/", booking_payload(location), format="json")
    assert response.status_code in (401, 403)


def test_create_list_retrieve_and_cancel_flow(logged, client_user, location):
    extra = Extra.objects.create(title="Проектор", hour_price=200)
    created = logged.post(f"{API}/bookings/", booking_payload(location, extras=[{"extra": extra.pk, "hours": 1}]), format="json")
    assert created.status_code == 201, created.content
    body = created.json()
    assert body["status"] == "pending"
    assert body["location"]["slug"] == "loft"
    assert body["extras"][0]["hours"] == 1
    assert body["can_cancel"] is True
    assert body["expires_at"] is not None

    assert [b["id"] for b in logged.get(f"{API}/bookings/").json()["results"]] == [body["id"]]
    assert logged.get(f"{API}/bookings/{body['id']}/").json()["id"] == body["id"]
    assert logged.get(f"{API}/bookings/", {"status": "confirmed"}).json()["results"] == []

    cancelled = logged.post(f"{API}/bookings/{body['id']}/cancel/")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert logged.post(f"{API}/bookings/{body['id']}/cancel/").status_code == 409


def test_busy_slot_returns_conflict(logged, location):
    first = logged.post(f"{API}/bookings/", booking_payload(location), format="json")
    second = logged.post(f"{API}/bookings/", booking_payload(location), format="json")
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["code"] == "slot_unavailable"


def test_validation_errors_are_reported(logged, location):
    bad_time = booking_payload(location, **future_slot(start=7, end=9))
    response = logged.post(f"{API}/bookings/", bad_time, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_time_range"

    assert logged.post(f"{API}/bookings/", {"location": "nope", **future_slot()}, format="json").status_code == 400


def test_client_sees_only_own_bookings(api, location, client_user, db):
    other = User.objects.create_user("other@example.com")
    foreign = services.create_booking(
        user=other,
        location=location,
        starts_at=datetime.fromisoformat(future_slot()["starts_at"]),
        ends_at=datetime.fromisoformat(future_slot()["ends_at"]),
    )
    api.force_authenticate(client_user)
    assert api.get(f"{API}/bookings/").json()["results"] == []
    assert api.get(f"{API}/bookings/{foreign.pk}/").status_code == 404
    assert api.post(f"{API}/bookings/{foreign.pk}/cancel/").status_code == 404
    assert Booking.objects.get(pk=foreign.pk).status == Booking.Status.PENDING


def test_late_cancellation_of_confirmed_booking_is_rejected(logged, client_user, location):
    start = datetime.fromisoformat(future_slot(days=0, start=23, end=24)["starts_at"])
    now = datetime.now(TZ)
    if start <= now + timedelta(hours=1):
        pytest.skip("слишком поздно для сценария")
    booking = services.create_booking(user=client_user, location=location, starts_at=start, ends_at=start + timedelta(hours=1))
    services.confirm_booking(booking)

    response = logged.post(f"{API}/bookings/{booking.pk}/cancel/")
    assert response.status_code == 409
    assert response.json()["code"] == "cancellation_not_allowed"
    assert logged.get(f"{API}/bookings/{booking.pk}/").json()["can_cancel"] is False


def test_openapi_schema_builds(api):
    response = api.get("/api/schema/")
    assert response.status_code == 200
