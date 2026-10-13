from dataclasses import dataclass, field
from typing import Protocol

from apps.core.errors import ApiError


class PaymentProviderError(ApiError):
    """Платёжный сервис недоступен или отклонил запрос."""

    code = "payment_provider_error"
    status_code = 502

    def __init__(self, message: str = "Платёжный сервис временно недоступен. Попробуйте позже.", *, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


@dataclass
class ProviderPayment:
    payment_id: str
    url: str
    status: str
    raw: dict = field(default_factory=dict)


class PaymentProvider(Protocol):
    name: str

    def init(self, payment, booking) -> ProviderPayment: ...

    def state(self, payment) -> str:
        """Статус платежа у провайдера (в терминах провайдера)."""

    def cancel(self, payment) -> str:
        """Отмена неоплаченного платежа или возврат оплаченного. Возвращает статус провайдера."""

    def verify_notification(self, payload: dict) -> bool: ...
