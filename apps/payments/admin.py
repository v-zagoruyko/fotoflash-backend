from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("created_at", "booking", "provider", "status", "amount")
    list_filter = ("status", "provider")
    search_fields = ("provider_payment_id", "booking__user__email")
    list_select_related = ("booking",)
    readonly_fields = [f.name for f in Payment._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
