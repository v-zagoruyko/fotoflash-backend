import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from rest_framework.test import APIClient

from apps.bookings import services as booking_services
from apps.bookings.models import Booking
from apps.payments import services
from apps.payments.models import Payment
from apps.payments.providers import PaymentProviderError, get_provider
from apps.payments.providers.base import ProviderPayment
from apps.payments.providers.tbank import TBankClient, TBankProvider, make_token, to_kopecks

TZ = ZoneInfo("Asia/Yekaterinburg")
NOW = datetime(2030, 1, 1, 12, tzinfo=TZ)
PASSWORD = "secret-password"
TERMINAL = "TestTerminal"


def at(hour):
    return datetime(2030, 1, 7, tzinfo=TZ) + timedelta(hours=hour)


@pytest.fixture
def booking(client_user, location):
    return booking_services.create_booking(user=client_user, location=location, starts_at=at(10), ends_at=at(12), now=NOW)


class FakeBank:
    """Имитация API банка: запоминает вызовы и отвечает по сценарию."""

    def __init__(self):
        self.calls = []
        self.state = "NEW"
        self.fail = False

    def __call__(self, url, body):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, body))
        assert body["Token"] == make_token({k: v for k, v in body.items() if k != "Token"}, PASSWORD)
        if self.fail:
            return {"Success": False, "ErrorCode": "99", "Message": "boom"}
        if method == "Init":
            return {
                "Success": True,
                "ErrorCode": "0",
                "Status": "NEW",
                "PaymentId": "5001",
                "PaymentURL": "https://pay.example/5001",
                "Amount": body["Amount"],
                "OrderId": body["OrderId"],
            }
        if method == "GetState":
            return {"Success": True, "Status": self.state, "PaymentId": body["PaymentId"]}
        if method == "Cancel":
            return {"Success": True, "Status": "REFUNDED" if self.state == "CONFIRMED" else "CANCELED"}
        raise AssertionError(method)


@pytest.fixture
def bank(monkeypatch, settings):
    settings.PAYMENT_PROVIDER = "tbank"
    settings.TBANK_NOTIFICATION_URL = "https://api.example/api/v1/payments/tbank/notification/"
    fake = FakeBank()
    client = TBankClient(terminal_key=TERMINAL, password=PASSWORD, transport=fake)
    monkeypatch.setattr("apps.payments.providers.PROVIDERS", {"tbank": lambda: TBankProvider(client)})
    return fake


# --- подпись и клиент -----------------------------------------------------------------------------


def test_token_follows_documented_algorithm():
    params = {"TerminalKey": "T", "Amount": 100000, "OrderId": "21050", "Description": "Описание", "Receipt": {"x": 1}, "Token": "ignored"}
    expected = hashlib.sha256("100000Описание21050PasswordT".replace("Password", "pw").encode()).hexdigest()
    # Порядок по алфавиту ключей: Amount, Description, OrderId, Password, TerminalKey
    expected = hashlib.sha256(b"100000" + "Описание".encode() + b"21050" + b"pw" + b"T").hexdigest()
    assert make_token(params, "pw") == expected


def test_token_stringifies_booleans_like_the_bank():
    assert make_token({"Success": True, "A": 1}, "p") == hashlib.sha256(b"1ptrue").hexdigest()


def test_kopecks_rounding():
    assert to_kopecks(Decimal("2999.99")) == 299999
    assert to_kopecks(Decimal("1500")) == 150000


def test_client_raises_on_bank_error(bank):
    bank.fail = True
    client = TBankClient(terminal_key=TERMINAL, password=PASSWORD, transport=bank)
    with pytest.raises(PaymentProviderError) as info:
        client.call("Init", {"Amount": 1})
    assert info.value.details["ErrorCode"] == "99"


def test_notification_signature_verification():
    client = TBankClient(terminal_key=TERMINAL, password=PASSWORD, transport=lambda *a: {})
    payload = {"TerminalKey": TERMINAL, "PaymentId": 1, "Status": "CONFIRMED", "Success": True, "Amount": 100}
    assert client.verify_notification({**payload, "Token": make_token(payload, PASSWORD)})
    assert not client.verify_notification({**payload, "Token": "forged"})
    assert not client.verify_notification(payload)


# --- создание платежа -----------------------------------------------------------------------------


def test_start_payment_builds_valid_init_request(bank, booking):
    payment = services.start_payment(booking, now=NOW)
    method, body = bank.calls[0]
    assert method == "Init"
    assert body["Amount"] == 300000
    assert body["OrderId"] == f"{booking.pk}-{payment.pk}"
    assert body["Receipt"]["Items"][0]["Amount"] == 300000
    assert body["Receipt"]["Email"] == "client@example.com"
    assert body["NotificationURL"].endswith("/payments/tbank/notification/")
    assert body["RedirectDueDate"].endswith("+05:00")
    assert "Password" not in body and TERMINAL == body["TerminalKey"]

    assert (payment.status, payment.payment_url, payment.provider_payment_id) == (
        Payment.Status.PENDING,
        "https://pay.example/5001",
        "5001",
    )


def test_start_payment_reuses_live_link(bank, booking):
    first = services.start_payment(booking, now=NOW)
    second = services.start_payment(booking, now=NOW)
    assert first.pk == second.pk
    assert len(bank.calls) == 1


def test_start_payment_rejects_bad_states(bank, booking):
    with pytest.raises(Exception, match="истекло"):
        services.start_payment(booking, now=NOW + timedelta(minutes=29))
    booking_services.confirm_booking(booking)
    with pytest.raises(Exception, match="не ждёт оплаты"):
        services.start_payment(booking, now=NOW)


def test_bank_failure_marks_payment_rejected(bank, booking):
    bank.fail = True
    with pytest.raises(PaymentProviderError):
        services.start_payment(booking, now=NOW)
    assert booking.payments.get().status == Payment.Status.REJECTED


# --- уведомления ----------------------------------------------------------------------------------


def signed(payment, status, **extra):
    payload = {
        "TerminalKey": TERMINAL,
        "OrderId": f"{payment.booking_id}-{payment.pk}",
        "Success": True,
        "Status": status,
        "PaymentId": int(payment.provider_payment_id),
        "ErrorCode": "0",
        "Amount": to_kopecks(payment.amount),
        **extra,
    }
    return {**payload, "Token": make_token(payload, PASSWORD)}


def test_confirmed_notification_confirms_booking_once(bank, booking, client_user):
    payment = services.start_payment(booking, now=NOW)
    api = APIClient()
    payload = signed(payment, "CONFIRMED")

    for _ in range(2):  # банк может прислать повторно
        response = api.post("/api/v1/payments/tbank/notification/", payload, format="json")
        assert response.status_code == 200
        assert response.content == b"OK"

    booking.refresh_from_db()
    payment.refresh_from_db()
    client_user.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert payment.status == Payment.Status.CONFIRMED
    assert client_user.bonus_balance == 90  # кешбэк начислен один раз


def test_forged_and_mismatched_notifications_are_ignored(bank, booking):
    payment = services.start_payment(booking, now=NOW)
    api = APIClient()

    forged = {**signed(payment, "CONFIRMED"), "Token": "bad"}
    assert api.post("/api/v1/payments/tbank/notification/", forged, format="json").status_code == 400

    wrong_amount = signed(payment, "CONFIRMED", Amount=100)
    assert api.post("/api/v1/payments/tbank/notification/", wrong_amount, format="json").status_code == 200

    booking.refresh_from_db()
    assert booking.status == Booking.Status.PENDING


def test_late_payment_for_expired_booking_is_refunded(bank, booking):
    payment = services.start_payment(booking, now=NOW)
    bank.state = "NEW"
    booking_services.expire_pending_bookings(now=NOW + timedelta(minutes=31))
    booking.refresh_from_db()
    assert booking.status == Booking.Status.EXPIRED
    assert [c[0] for c in bank.calls].count("Cancel") == 1  # открытый заказ отменён в банке

    # банк всё же прислал CONFIRMED (оплата прошла в последнюю секунду)
    bank.state = "CONFIRMED"
    payment.refresh_from_db()
    payment.status = Payment.Status.PENDING
    payment.save()
    services.apply_status(payment, "CONFIRMED")

    payment.refresh_from_db()
    assert payment.status == Payment.Status.REFUNDED
    assert [c[0] for c in bank.calls].count("Cancel") == 2


# --- снятие холда и отмена через банк -------------------------------------------------------------


def test_hold_release_cancels_order_in_bank_first(bank, booking):
    payment = services.start_payment(booking, now=NOW)
    assert booking_services.expire_pending_bookings(now=NOW + timedelta(minutes=31)) == 1
    assert [c[0] for c in bank.calls][-2:] == ["GetState", "Cancel"]
    payment.refresh_from_db()
    booking.refresh_from_db()
    assert payment.status == Payment.Status.EXPIRED
    assert booking.status == Booking.Status.EXPIRED


def test_hold_is_kept_when_bank_is_unreachable(bank, booking):
    services.start_payment(booking, now=NOW)
    bank.fail = True
    assert booking_services.expire_pending_bookings(now=NOW + timedelta(minutes=31)) == 0
    booking.refresh_from_db()
    assert booking.status == Booking.Status.PENDING  # повторим при следующей проверке
    bank.fail = False
    assert booking_services.expire_pending_bookings(now=NOW + timedelta(minutes=32)) == 1


def test_paid_at_last_second_is_confirmed_instead_of_expired(bank, booking):
    services.start_payment(booking, now=NOW)
    bank.state = "CONFIRMED"
    assert booking_services.expire_pending_bookings(now=NOW + timedelta(minutes=31)) == 0
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED


def test_cancel_confirmed_booking_refunds_via_bank(bank, booking, client_user):
    payment = services.start_payment(booking, now=NOW)
    services.apply_status(payment, "CONFIRMED")
    bank.state = "CONFIRMED"

    booking_services.cancel_booking(booking, now=NOW)

    booking.refresh_from_db()
    payment.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
    assert payment.status == Payment.Status.REFUNDED


def test_cancel_is_aborted_when_refund_fails(bank, booking):
    payment = services.start_payment(booking, now=NOW)
    services.apply_status(payment, "CONFIRMED")
    bank.fail = True
    with pytest.raises(PaymentProviderError):
        booking_services.cancel_booking(booking, now=NOW)
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED


def test_reconcile_picks_up_missed_notification_and_orphaned_payments(bank, booking):
    payment = services.start_payment(booking, now=NOW)
    bank.state = "CONFIRMED"
    result = services.reconcile_payments(now=NOW + timedelta(minutes=5))
    booking.refresh_from_db()
    assert result["checked"] == 1
    assert booking.status == Booking.Status.CONFIRMED
    payment.refresh_from_db()
    assert payment.status == Payment.Status.CONFIRMED


# --- API ------------------------------------------------------------------------------------------


def test_pay_endpoint_returns_payment_url(bank, client_user, location):
    api = APIClient()
    api.force_authenticate(client_user)
    start = (datetime.now(TZ) + timedelta(days=30)).replace(hour=10, minute=0, second=0, microsecond=0)
    created = api.post(
        "/api/v1/bookings/",
        {"location": location.slug, "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=2)).isoformat()},
        format="json",
    )
    assert created.json()["payment_url"] is None

    paid = api.post(f"/api/v1/bookings/{created.json()['id']}/pay/")
    assert paid.status_code == 200, paid.content
    assert paid.json()["payment_url"] == "https://pay.example/5001"


def test_fake_provider_flow_in_dev(client_user, location, settings):
    settings.PAYMENT_PROVIDER = "fake"
    booking = booking_services.create_booking(user=client_user, location=location, starts_at=at(10), ends_at=at(12), now=NOW)
    payment = services.start_payment(booking, now=NOW)
    assert payment.provider == "fake" and payment.payment_url.startswith("http")
    assert get_provider("fake").state(payment) == "NEW"
    services.apply_status(payment, "CONFIRMED")
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED


def test_provider_choice_is_stored_per_payment(bank, booking):
    payment = services.start_payment(booking, now=NOW)
    assert payment.provider == "tbank"
    assert isinstance(ProviderPayment("1", "u", "NEW"), ProviderPayment)
    json.dumps(payment.raw)  # сырой ответ банка сериализуем
