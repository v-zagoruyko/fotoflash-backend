from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse

from .models import StudioSettings


@admin.register(StudioSettings)
class StudioSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Бронирование", {"fields": ("hold_minutes", "cancel_before_hours", "reminder_before_hours")}),
        ("Контакты для писем клиентам", {"fields": ("address", "contact_phone")}),
        ("Бонусная программа", {"fields": ("cashback_percent", "cashback_on_bonus_bookings", "max_bonus_share_percent")}),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = StudioSettings.load()
        return redirect(reverse("admin:core_studiosettings_change", args=[obj.pk]))
