"""Клиент эквайринга Т-Банка (API v2).

Сертификат банка выпущен Russian Trusted Sub CA (Минцифры), которого нет в обычных хранилищах доверия.
Поэтому запросы идут через отдельный SSL-контекст: системные корни плюс бандл из `apps/payments/certs`.
Глобальное хранилище не меняется и проверка сертификата не отключается.
Срок действия: корень до 2032, промежуточный (2024) до 2029, промежуточный (2022) до 2027.
"""

import hashlib
import hmac
import json
import logging
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .base import PaymentProviderError, ProviderPayment

logger = logging.getLogger(__name__)

DEFAULT_CA_BUNDLE = Path(__file__).resolve().parent.parent / "certs" / "russian_trusted_ca_bundle.pem"
ITEM_NAME = "Аренда фотостудии и оборудования"


def to_kopecks(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _stringify(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def make_token(params: dict, password: str) -> str:
    """Подпись запроса: значения корневых скалярных параметров + Password, по алфавиту ключей, SHA-256."""
    data = {k: v for k, v in params.items() if k != "Token" and v is not None and not isinstance(v, dict | list)}
    data["Password"] = password
    return hashlib.sha256("".join(_stringify(data[k]) for k in sorted(data)).encode()).hexdigest()


def _urllib_transport(ca_bundle: Path, timeout: int) -> Callable[[str, dict], dict]:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(ca_bundle))

    def send(url: str, body: dict) -> dict:
        request = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read())
            except ValueError as parse_error:
                raise PaymentProviderError(details={"http_status": exc.code}) from parse_error
        except (urllib.error.URLError, TimeoutError, ssl.SSLError, ValueError) as exc:
            logger.warning("T-Bank request failed: %s", exc)
            raise PaymentProviderError(details={"error": str(exc)}) from exc

    return send


class TBankClient:
    def __init__(
        self,
        *,
        terminal_key: str,
        password: str,
        api_url: str = "https://securepay.tbank.ru/v2",
        ca_bundle: Path = DEFAULT_CA_BUNDLE,
        timeout: int = 15,
        transport: Callable[[str, dict], dict] | None = None,
    ):
        self.terminal_key = terminal_key
        self.password = password
        self.api_url = api_url.rstrip("/")
        self.transport = transport or _urllib_transport(ca_bundle, timeout)

    def call(self, method: str, payload: dict) -> dict:
        body = {"TerminalKey": self.terminal_key, **payload}
        body["Token"] = make_token(body, self.password)
        data = self.transport(f"{self.api_url}/{method}", body)
        if not data.get("Success"):
            logger.warning("T-Bank %s rejected: code=%s message=%s", method, data.get("ErrorCode"), data.get("Message"))
            raise PaymentProviderError(
                "Платёжный сервис отклонил запрос.",
                details={"ErrorCode": data.get("ErrorCode"), "Message": data.get("Message"), "Details": data.get("Details")},
            )
        return data

    def verify_notification(self, payload: dict) -> bool:
        token = str(payload.get("Token", ""))
        return bool(token) and hmac.compare_digest(token, make_token(payload, self.password))


class TBankProvider:
    name = "tbank"

    def __init__(self, client: TBankClient | None = None):
        self.client = client or TBankClient(
            terminal_key=settings.TBANK_TERMINAL_KEY,
            password=settings.TBANK_PASSWORD,
            api_url=settings.TBANK_API_URL,
        )

    def init(self, payment, booking) -> ProviderPayment:
        user = booking.user
        amount = to_kopecks(payment.amount)
        receipt = {
            "Email": user.email,
            "Taxation": settings.TBANK_TAXATION,
            "Items": [
                {
                    "Name": ITEM_NAME,
                    "Price": amount,
                    "Quantity": 1,
                    "Amount": amount,
                    "Tax": settings.TBANK_ITEM_TAX,
                    "PaymentMethod": "full_payment",
                    "PaymentObject": "service",
                }
            ],
        }
        if user.phone:
            receipt["Phone"] = user.phone
        if settings.TBANK_EMAIL_COMPANY:
            receipt["EmailCompany"] = settings.TBANK_EMAIL_COMPANY

        starts = timezone.localtime(booking.starts_at)
        payload = {
            "Amount": amount,
            "OrderId": f"{booking.pk}-{payment.pk}",
            "Description": f"Бронь №{booking.pk}: {booking.location.title}, {starts:%d.%m.%Y %H:%M}"[:140],
            "NotificationURL": settings.TBANK_NOTIFICATION_URL,
            "SuccessURL": f"{settings.FRONTEND_URL}/profile/bookings/{booking.pk}?payment=success",
            "FailURL": f"{settings.FRONTEND_URL}/profile/bookings/{booking.pk}?payment=fail",
            "Receipt": receipt,
        }
        if booking.expires_at:
            payload["RedirectDueDate"] = timezone.localtime(booking.expires_at).isoformat(timespec="seconds")
        payload = {k: v for k, v in payload.items() if v}

        data = self.client.call("Init", payload)
        return ProviderPayment(payment_id=str(data["PaymentId"]), url=data["PaymentURL"], status=data.get("Status", "NEW"), raw=data)

    def state(self, payment) -> str:
        return self.client.call("GetState", {"PaymentId": payment.provider_payment_id})["Status"]

    def cancel(self, payment) -> str:
        return self.client.call("Cancel", {"PaymentId": payment.provider_payment_id})["Status"]

    def verify_notification(self, payload: dict) -> bool:
        return self.client.verify_notification(payload)
