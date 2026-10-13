from django.contrib.auth import login, logout
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.errors import ApiError

from . import auth_codes
from .models import User
from .serializers import BonusTransactionSerializer, CodeRequestSerializer, CodeVerifySerializer, UserSerializer
from .tasks import send_login_code


class ConsentRequired(ApiError):
    code = "consent_required"


class InactiveAccount(ApiError):
    code = "account_disabled"
    status_code = 403


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        responses=inline_serializer("Csrf", {"csrfToken": serializers.CharField()}),
        description="Выставляет cookie `csrftoken`. Значение нужно передавать в заголовке `X-CSRFToken` в POST/PATCH-запросах.",
    )
    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class CodeRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(
        request=CodeRequestSerializer,
        responses={204: OpenApiResponse(description="Код отправлен (ответ одинаков, есть ли такой клиент или нет)")},
    )
    def post(self, request):
        serializer = CodeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        code = auth_codes.issue_code(email)
        send_login_code.delay(email, code)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CodeVerifyView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(
        request=CodeVerifySerializer,
        responses=UserSerializer,
        description="Проверяет код и открывает сессию (cookie). Новый клиент создаётся автоматически, "
        "для него обязательно `consent: true`.",
    )
    def post(self, request):
        serializer = CodeVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        email = data["email"]

        existing = User.objects.filter(email__iexact=email).first()
        if existing is not None and not existing.is_active:
            raise InactiveAccount("Аккаунт отключён. Свяжитесь со студией.")
        if (existing is None or existing.consent_at is None) and not data["consent"]:
            raise ConsentRequired("Нужно согласие на обработку персональных данных.")

        auth_codes.check_code(email, data["code"])

        user = existing or User.objects.create_user(email)
        update_fields = []
        if data.get("name") and not user.name:
            user.name = data["name"].strip()
            update_fields.append("name")
        if user.consent_at is None and data["consent"]:
            user.consent_at = timezone.now()
            update_fields.append("consent_at")
        if update_fields:
            user.save(update_fields=update_fields)

        login(request, user)
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=None, responses={204: OpenApiResponse(description="Сессия завершена")})
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=UserSerializer)
    def get(self, request):
        return Response(UserSerializer(request.user).data)

    @extend_schema(request=UserSerializer, responses=UserSerializer)
    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class BonusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=inline_serializer(
            "Bonus",
            {"balance": serializers.IntegerField(), "transactions": BonusTransactionSerializer(many=True)},
        )
    )
    def get(self, request):
        transactions = request.user.bonus_transactions.all()[:50]
        return Response({"balance": request.user.bonus_balance, "transactions": BonusTransactionSerializer(transactions, many=True).data})
