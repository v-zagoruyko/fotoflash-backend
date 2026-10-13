from adminsortable2.admin import SortableAdminMixin, SortableTabularInline
from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html

from .models import Extra, ExtraCategory, Location, LocationImage


def thumbnail(image, size=56):
    if not image:
        return "—"
    return format_html('<img src="{}" style="height:{}px;width:auto;border-radius:4px">', image.url, size)


class LocationImageInline(SortableTabularInline):
    model = LocationImage
    extra = 1
    fields = ("image", "preview")
    readonly_fields = ("preview",)

    @admin.display(description="Предпросмотр")
    def preview(self, obj):
        return thumbnail(obj.image) if obj.pk else "—"


@admin.register(Location)
class LocationAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = ("cover_preview", "title", "hour_price", "late_surcharge", "weekend_surcharge", "is_active")
    list_display_links = ("cover_preview", "title")
    list_editable = ("is_active",)
    list_filter = ("is_active",)
    search_fields = ("title",)
    filter_horizontal = ("conflicts_with",)
    autocomplete_fields = ("rules_document",)
    inlines = (LocationImageInline,)

    fieldsets = (
        (None, {"fields": ("title", "slug", "description", "cover", "rules_document", "is_active")}),
        (
            "Цены",
            {
                "description": (
                    "Итоговая цена часа = базовая цена + доплата за поздний час (если час попадает "
                    "в поздний тариф) + доплата за выходной (если суббота или воскресенье)."
                ),
                "fields": ("hour_price", "late_from", "late_surcharge", "weekend_surcharge"),
            },
        ),
        ("Режим работы", {"fields": ("open_time", "close_time", "min_hours")}),
        (
            "Смежные залы",
            {
                "description": "Если зал занят, смежные с ним залы тоже недоступны для брони на это время.",
                "fields": ("conflicts_with",),
            },
        ),
    )

    @admin.display(description="Фото")
    def cover_preview(self, obj):
        return thumbnail(obj.cover)


@admin.register(ExtraCategory)
class ExtraCategoryAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = ("title", "extras_count")
    search_fields = ("title",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(extras_total=Count("extras"))

    @admin.display(description="Позиций", ordering="extras_total")
    def extras_count(self, obj):
        return obj.extras_total


@admin.register(Extra)
class ExtraAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = ("cover_preview", "title", "category", "hour_price", "quantity", "is_active")
    list_display_links = ("cover_preview", "title")
    list_editable = ("category", "hour_price", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("title",)

    @admin.display(description="Фото")
    def cover_preview(self, obj):
        return thumbnail(obj.cover)
