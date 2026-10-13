from django.contrib import admin
from django.utils.html import format_html

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "file_link", "is_published", "updated_at")
    list_editable = ("is_published",)
    search_fields = ("title", "slug")
    readonly_fields = ("updated_at",)

    @admin.display(description="Файл")
    def file_link(self, obj):
        if not obj.file:
            return "—"
        return format_html('<a href="{}" target="_blank">Открыть PDF</a>', obj.file.url)
