from datetime import timedelta

from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.bookings import services

from .models import Extra, Location
from .serializers import (
    AvailabilityQuerySerializer,
    DayAvailabilitySerializer,
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
        responses=DayAvailabilitySerializer(many=True),
        description=(
            "Почасовые слоты зала с признаком доступности и ценой часа. Учитывает смежные залы и неоплаченные брони с живым холдом."
        ),
    )
    @action(detail=True, methods=["get"])
    def availability(self, request, slug=None):
        params = AvailabilityQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        location = self.get_object()
        start = params.validated_data["date"]
        days = [
            {"date": day, "slots": services.get_day_slots(location, day)}
            for day in (start + timedelta(days=i) for i in range(params.validated_data["days"]))
        ]
        return Response(DayAvailabilitySerializer(days, many=True).data)


class ExtraViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    serializer_class = ExtraSerializer
    queryset = Extra.objects.filter(is_active=True)
    pagination_class = None
