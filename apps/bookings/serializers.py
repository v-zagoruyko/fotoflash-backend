from datetime import datetime, timedelta

from django.utils import timezone
from rest_framework import serializers

from apps.catalog.models import Location

from .models import Booking, BookingExtra
from .services import ExtraOrder


class ExtraOrderSerializer(serializers.Serializer):
    extra = serializers.IntegerField(help_text="ID оборудования или услуги.")
    hours = serializers.IntegerField(required=False, min_value=1, help_text="Сколько часов нужна позиция. По умолчанию на всё время брони.")


class BookingInputSerializer(serializers.Serializer):
    location = serializers.SlugRelatedField(slug_field="slug", queryset=Location.objects.filter(is_active=True))
    starts_at = serializers.DateTimeField(help_text="Начало, на целый час. Без часового пояса считается временем студии.")
    ends_at = serializers.DateTimeField(help_text="Конец, не включительно.")
    extras = ExtraOrderSerializer(many=True, required=False)
    bonus_points = serializers.IntegerField(required=False, default=0, min_value=0, help_text="Сколько баллов списать.")

    def service_kwargs(self) -> dict:
        data = self.validated_data
        return {
            "location": data["location"],
            "starts_at": data["starts_at"],
            "ends_at": data["ends_at"],
            "extras": [ExtraOrder(e["extra"], e.get("hours")) for e in data.get("extras", [])],
            "bonus_points": data.get("bonus_points", 0),
        }


class QuoteExtraLineSerializer(serializers.Serializer):
    extra = serializers.IntegerField()
    title = serializers.CharField()
    hours = serializers.IntegerField()
    unit_price = serializers.DecimalField(max_digits=8, decimal_places=2)
    amount = serializers.DecimalField(max_digits=9, decimal_places=2)


class QuoteSerializer(serializers.Serializer):
    location = serializers.CharField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    hours = serializers.IntegerField()
    location_amount = serializers.DecimalField(max_digits=9, decimal_places=2)
    extras = QuoteExtraLineSerializer(many=True)
    subtotal = serializers.DecimalField(max_digits=9, decimal_places=2)
    bonus_points = serializers.IntegerField()
    total = serializers.DecimalField(max_digits=9, decimal_places=2)
    max_bonus_points = serializers.IntegerField(help_text="Сколько баллов клиент может списать на эту бронь (0 для гостя).")
    breakdown = serializers.DictField(help_text="Разбивка: базовая цена, доплаты за поздние часы и выходные.")


class BookingExtraSerializer(serializers.ModelSerializer):
    extra = serializers.IntegerField(source="extra_id")
    title = serializers.CharField(source="extra.title")

    class Meta:
        model = BookingExtra
        fields = ("extra", "title", "hours", "unit_price", "amount")


class BookingLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ("slug", "title")


class BookingSerializer(serializers.ModelSerializer):
    """Номер брони — её `id`."""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    location = BookingLocationSerializer(read_only=True)
    extras = BookingExtraSerializer(many=True, read_only=True)
    payment_url = serializers.SerializerMethodField(help_text="Ссылка на оплату, пока бронь ждёт оплаты и ссылка выдана.")
    can_cancel = serializers.SerializerMethodField(help_text="Может ли клиент отменить бронь сам.")
    cancel_deadline = serializers.SerializerMethodField(help_text="До какого момента клиент может отменить оплаченную бронь.")

    class Meta:
        model = Booking
        fields = (
            "id",
            "status",
            "status_display",
            "location",
            "starts_at",
            "ends_at",
            "expires_at",
            "subtotal",
            "bonus_spent",
            "total",
            "extras",
            "payment_url",
            "can_cancel",
            "cancel_deadline",
            "created_at",
            "cancelled_at",
        )

    def _deadline(self, obj):
        hours = self.context.get("cancel_before_hours", 24)
        return obj.starts_at - timedelta(hours=hours)

    def get_payment_url(self, obj) -> str | None:
        if obj.status != Booking.Status.PENDING:
            return None
        for payment in obj.payments.all():
            if payment.status == "pending" and payment.payment_url:
                return payment.payment_url
        return None

    def get_can_cancel(self, obj) -> bool:
        if obj.status == Booking.Status.PENDING:
            return True
        return obj.status == Booking.Status.CONFIRMED and timezone.now() <= self._deadline(obj)

    def get_cancel_deadline(self, obj) -> datetime | None:
        if obj.status != Booking.Status.CONFIRMED:
            return None
        return self._deadline(obj)


def quote_payload(quote) -> dict:
    return {
        "location": quote.location.slug,
        "starts_at": quote.starts_at,
        "ends_at": quote.ends_at,
        "hours": quote.price.hours,
        "location_amount": quote.price.location_amount,
        "extras": [
            {
                "extra": line.extra.pk,
                "title": line.extra.title,
                "hours": line.hours,
                "unit_price": line.unit_price,
                "amount": line.amount,
            }
            for line in quote.price.extra_lines
        ],
        "subtotal": quote.price.subtotal,
        "bonus_points": quote.bonus_points,
        "total": quote.total,
        "max_bonus_points": quote.max_bonus_points,
        "breakdown": quote.price.breakdown,
    }
