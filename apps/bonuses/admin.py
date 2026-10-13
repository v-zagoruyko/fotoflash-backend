from django.contrib import admin

from .models import BonusTransaction


@admin.register(BonusTransaction)
class BonusTransactionAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("created_at", "user", "kind", "points", "booking", "comment")
    list_filter = ("kind",)
    search_fields = ("user__email", "user__name", "comment")
    list_select_related = ("user", "booking")
    readonly_fields = [f.name for f in BonusTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
