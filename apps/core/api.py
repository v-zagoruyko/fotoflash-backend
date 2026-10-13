from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .errors import ApiError


def exception_handler(exc, context):
    """Единый формат ошибок: {"detail": "...", "code": "..."}. Валидация DRF остаётся в стандартном виде."""
    if isinstance(exc, ApiError):
        return Response({"detail": exc.message, "code": exc.code}, status=exc.status_code)
    return drf_exception_handler(exc, context)
