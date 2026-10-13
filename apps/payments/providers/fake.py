"""Тестовый провайдер для разработки: платёж «оплачивается» командой `simulate_payment`."""

from django.conf import settings

from .base import ProviderPayment


class FakeProvider:
    name = "fake"

    def init(self, payment, booking) -> ProviderPayment:
        payment_id = f"fake-{payment.pk}"
        return ProviderPayment(
            payment_id=payment_id,
            url=f"{settings.FRONTEND_URL}/dev/pay/{payment_id}",
            status="NEW",
            raw={"fake": True},
        )

    def state(self, payment) -> str:
        return payment.raw.get("fake_status", "NEW")

    def cancel(self, payment) -> str:
        return "REFUNDED" if payment.raw.get("fake_status") == "CONFIRMED" else "CANCELED"

    def verify_notification(self, payload: dict) -> bool:
        return True
