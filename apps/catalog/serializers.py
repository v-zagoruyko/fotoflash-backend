from rest_framework import serializers

from apps.documents.serializers import DocumentSerializer

from .models import Extra, Location, LocationImage


class LocationImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = LocationImage
        fields = ("id", "image")


class LocationSerializer(serializers.ModelSerializer):
    images = LocationImageSerializer(many=True, read_only=True)
    rules_document = DocumentSerializer(read_only=True)

    class Meta:
        model = Location
        fields = (
            "id",
            "slug",
            "title",
            "description",
            "cover",
            "images",
            "rules_document",
            "hour_price",
            "late_from",
            "late_surcharge",
            "weekend_surcharge",
            "open_time",
            "close_time",
            "min_hours",
        )


class ExtraSerializer(serializers.ModelSerializer):
    class Meta:
        model = Extra
        fields = ("id", "title", "description", "cover", "hour_price")


class AvailabilityQuerySerializer(serializers.Serializer):
    date = serializers.DateField(help_text="Первая дата, YYYY-MM-DD.")
    days = serializers.IntegerField(required=False, default=1, min_value=1, max_value=14, help_text="Сколько дней вернуть, 1–14.")


class HourSlotSerializer(serializers.Serializer):
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    is_available = serializers.BooleanField()
    price = serializers.DecimalField(max_digits=9, decimal_places=2)


class DayAvailabilitySerializer(serializers.Serializer):
    date = serializers.DateField()
    slots = HourSlotSerializer(many=True)
