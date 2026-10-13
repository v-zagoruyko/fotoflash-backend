"""Одноразовые коды входа по email. Хранятся в кеше (Redis) в виде HMAC и живут ограниченное время."""

import hashlib
import hmac
import secrets

from django.conf import settings
from django.core.cache import cache

from apps.core.errors import ApiError

CODE_TTL = 10 * 60
RESEND_COOLDOWN = 60
HOURLY_LIMIT = 5
MAX_ATTEMPTS = 5


class TooManyRequests(ApiError):
    code = "too_many_requests"
    status_code = 429


class InvalidCode(ApiError):
    code = "invalid_code"


def _key(kind: str, email: str) -> str:
    return f"authcode:{kind}:{email}"


def _digest(email: str, code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def issue_code(email: str) -> str:
    """Генерирует код и запоминает его. Бросает TooManyRequests при слишком частых запросах."""
    if not cache.add(_key("cooldown", email), 1, RESEND_COOLDOWN):
        raise TooManyRequests("Код уже отправлен. Повторный запрос возможен через минуту.")
    hourly = _key("hourly", email)
    cache.add(hourly, 0, 3600)
    if cache.incr(hourly) > HOURLY_LIMIT:
        raise TooManyRequests("Слишком много запросов кода. Попробуйте через час.")

    code = f"{secrets.randbelow(10**6):06d}"
    cache.set(_key("code", email), {"digest": _digest(email, code), "attempts": 0}, CODE_TTL)
    return code


def check_code(email: str, code: str) -> None:
    """Проверяет код. Успешная проверка одноразовая: код удаляется."""
    key = _key("code", email)
    data = cache.get(key)
    if not data:
        raise InvalidCode("Код истёк или не запрашивался. Запросите новый.")
    if data["attempts"] >= MAX_ATTEMPTS:
        cache.delete(key)
        raise InvalidCode("Слишком много неверных попыток. Запросите новый код.")
    if not hmac.compare_digest(data["digest"], _digest(email, code.strip())):
        data["attempts"] += 1
        cache.set(key, data, CODE_TTL)
        raise InvalidCode("Неверный код.")
    cache.delete(key)
