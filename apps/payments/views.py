import json
import logging

from django.conf import settings
from django.http import Http404, HttpResponse
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import Payment
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


class SimulatePaymentView(APIView):
    """Только для DEBUG: имитация ответа банка для тестового платежа своей брони."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        exclude=not settings.DEBUG,
        request=inline_serializer(
            "SimulatePayment", {"status": serializers.ChoiceField(["CONFIRMED", "REJECTED", "CANCELED"], default="CONFIRMED")}
        ),
        responses=inline_serializer(
            "SimulatedPayment", {"payment_status": serializers.CharField(), "booking_status": serializers.CharField()}
        ),
    )
    def post(self, request, payment_id: int):
        if not settings.DEBUG:
            raise Http404
        payment = get_object_or_404(
            Payment.objects.select_related("booking"), pk=payment_id, booking__user=request.user, provider=Payment.Provider.FAKE
        )
        body = request.data if isinstance(request.data, dict) else {}
        status = body.get("status", "CONFIRMED")
        if status not in ("CONFIRMED", "REJECTED", "CANCELED"):
            return Response({"detail": "Неизвестный статус.", "code": "invalid_status"}, status=400)
        payment = services.simulate_fake_payment(payment, status)
        return Response({"payment_status": payment.status, "booking_status": payment.booking.status})
