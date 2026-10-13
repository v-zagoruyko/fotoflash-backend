from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse

from .models import StudioSettings


@admin.register(StudioSettings)
class StudioSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Бронирование", {"fields": ("hold_minutes", "cancel_before_hours", "reminder_before_hours", "booking_horizon_days")}),
        ("Контакты студии", {"fields": ("address", "contact_phone", "contact_email", "map_url")}),
        (
            "Реквизиты исполнителя",
            {
                "description": "Раскрываются в футере сайта (закон о защите прав потребителей).",
                "fields": ("legal_name", "inn", "ogrnip", "legal_address"),
            },
        ),
        ("Бонусная программа", {"fields": ("cashback_percent", "cashback_on_bonus_bookings", "max_bonus_share_percent")}),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = StudioSettings.load()
        return redirect(reverse("admin:core_studiosettings_change", args=[obj.pk]))
