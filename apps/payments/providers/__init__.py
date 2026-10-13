from .base import PaymentProvider, PaymentProviderError, ProviderPayment
from .fake import FakeProvider
from .tbank import TBankProvider

PROVIDERS = {"tbank": TBankProvider, "fake": FakeProvider}


def get_provider(name: str) -> PaymentProvider:
    try:
        return PROVIDERS[name]()
    except KeyError:
        raise PaymentProviderError(f"Неизвестный платёжный провайдер: {name}") from None


__all__ = ["PROVIDERS", "PaymentProvider", "PaymentProviderError", "ProviderPayment", "get_provider"]
