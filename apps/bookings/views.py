from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.core.models import StudioSettings

from . import services
from .models import Booking
from .serializers import BookingInputSerializer, BookingSerializer, QuoteSerializer, quote_payload


class BookingViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Брони текущего клиента. Номер брони — `id`."""

    serializer_class = BookingSerializer
    lookup_value_regex = r"\d+"

    def get_permissions(self):
        if self.action == "quote":
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self):
        queryset = Booking.objects.filter(user=self.request.user).select_related("location").prefetch_related("extras__extra", "payments")
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status__in=status_filter.split(","))
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["cancel_before_hours"] = StudioSettings.load().cancel_before_hours
        return context

    @extend_schema(request=BookingInputSerializer, responses=QuoteSerializer)
    @action(detail=False, methods=["post"])
    def quote(self, request):
        """Проверяет запрос и считает цену без создания брони. Доступно без входа (баллы только после входа)."""
        serializer = BookingInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user if request.user.is_authenticated else None
        quote = services.quote_booking(user=user, **serializer.service_kwargs())
        return Response(QuoteSerializer(quote_payload(quote)).data)

    @extend_schema(request=BookingInputSerializer, responses={201: BookingSerializer})
    def create(self, request):
        """Создаёт неоплаченную бронь и удерживает слот на время, заданное в настройках студии."""
        serializer = BookingInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = services.create_booking(user=request.user, **serializer.service_kwargs())
        booking = self.get_queryset().get(pk=booking.pk)
        return Response(self.get_serializer(booking).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=BookingSerializer)
    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        """Выдаёт ссылку на оплату (`payment_url`) для неоплаченной брони. Повторный вызов отдаёт ту же ссылку, пока она жива."""
        from apps.payments import services as payment_services

        booking = self.get_object()
        payment_services.start_payment(booking)
        booking = self.get_queryset().get(pk=booking.pk)
        return Response(self.get_serializer(booking).data)

    @extend_schema(request=None, responses=BookingSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Отмена клиентом: неоплаченной — всегда, оплаченной — не позднее чем за N часов до начала."""
        booking = self.get_object()
        services.cancel_booking(booking)
        booking = self.get_queryset().get(pk=booking.pk)
        return Response(self.get_serializer(booking).data)
