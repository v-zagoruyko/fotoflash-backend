import json
import logging

from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from . import services
from .providers import get_provider

logger = logging.getLogger(__name__)


class TBankNotificationView(APIView):
    """Уведомления Т-Банка. Подлинность проверяется подписью (Token), ответ банку — текст `OK`."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(exclude=True)
    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else json.loads(request.body or "{}")
        if not get_provider("tbank").verify_notification(dict(payload)):
            logger.warning("T-Bank notification with invalid token: PaymentId=%s", payload.get("PaymentId"))
            return HttpResponse("INVALID TOKEN", status=400, content_type="text/plain")
        services.handle_notification(dict(payload))
        return HttpResponse("OK", content_type="text/plain")
