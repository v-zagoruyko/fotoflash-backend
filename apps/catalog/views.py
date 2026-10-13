from datetime import timedelta

from django.db.models import F
from drf_spectacular.utils import PolymorphicProxySerializer, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.bookings import services

from .models import Extra, Location
from .serializers import (
    AvailabilityQuerySerializer,
    DayAvailabilitySerializer,
    DaySummarySerializer,
    ExtraSerializer,
    LocationSerializer,
)


class LocationViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    serializer_class = LocationSerializer
    lookup_field = "slug"
    pagination_class = None

    def get_queryset(self):
        return Location.objects.filter(is_active=True).select_related("rules_document").prefetch_related("images")

    @extend_schema(
        parameters=[AvailabilityQuerySerializer],
        responses=PolymorphicProxySerializer(
            component_name="Availability",
            serializers=[DayAvailabilitySerializer, DaySummarySerializer],
            resource_type_field_name=None,
            many=True,
        ),
        description=(
            "Почасовые слоты зала с признаком доступности и ценой часа. Учитывает смежные залы и неоплаченные брони с живым холдом. "
            "С `summary=1` вместо слотов отдаёт `[{date, free_hours}]` и допускает до 93 дней."
        ),
    )
    @action(detail=True, methods=["get"])
    def availability(self, request, slug=None):
        params = AvailabilityQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        location = self.get_object()
        start = params.validated_data["date"]
        if params.validated_data["summary"]:
            summary = services.get_free_hours_by_day(location, start, params.validated_data["days"])
            return Response(DaySummarySerializer(summary, many=True).data)
        days = [
            {"date": day, "slots": services.get_day_slots(location, day)}
            for day in (start + timedelta(days=i) for i in range(params.validated_data["days"]))
        ]
        return Response(DayAvailabilitySerializer(days, many=True).data)


class ExtraViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    serializer_class = ExtraSerializer
    queryset = (
        Extra.objects.filter(is_active=True)
        .select_related("category")
        .order_by(F("category__sort").asc(nulls_last=True), "category_id", "sort", "title")
    )
    pagination_class = None
