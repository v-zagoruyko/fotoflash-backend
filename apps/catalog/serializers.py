from rest_framework import serializers

from apps.documents.serializers import DocumentSerializer

from .models import Extra, ExtraCategory, Location, LocationImage

MAX_SLOTS_DAYS = 14
MAX_SUMMARY_DAYS = 93


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


class ExtraCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtraCategory
        fields = ("id", "title")


class ExtraSerializer(serializers.ModelSerializer):
    category = ExtraCategorySerializer(read_only=True, allow_null=True)

    class Meta:
        model = Extra
        fields = ("id", "title", "description", "cover", "category", "hour_price")


class AvailabilityQuerySerializer(serializers.Serializer):
    date = serializers.DateField(help_text="Первая дата, YYYY-MM-DD.")
    days = serializers.IntegerField(
        required=False,
        default=1,
        min_value=1,
        max_value=MAX_SUMMARY_DAYS,
        help_text=f"Сколько дней вернуть: 1–{MAX_SLOTS_DAYS} со слотами, 1–{MAX_SUMMARY_DAYS} при summary=1.",
    )
    summary = serializers.BooleanField(
        required=False,
        default=False,
        help_text="1 — вместо слотов отдать только число свободных часов за день.",
    )

    def validate(self, attrs):
        if not attrs["summary"] and attrs["days"] > MAX_SLOTS_DAYS:
            raise serializers.ValidationError(
                {"days": f"Со слотами можно запросить не более {MAX_SLOTS_DAYS} дней, для большего диапазона добавьте summary=1."}
            )
        return attrs


class DaySummarySerializer(serializers.Serializer):
    date = serializers.DateField()
    free_hours = serializers.IntegerField(help_text="Сколько часов в этот день свободно для брони.")


class HourSlotSerializer(serializers.Serializer):
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    is_available = serializers.BooleanField()
    price = serializers.DecimalField(max_digits=9, decimal_places=2)


class DayAvailabilitySerializer(serializers.Serializer):
    date = serializers.DateField()
    slots = HourSlotSerializer(many=True)
