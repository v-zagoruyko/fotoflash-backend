from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm

from apps.bonuses.models import BonusTransaction
from apps.bookings.models import Booking

from .models import User


class UserCreationForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ("email", "name", "phone")


class BonusTransactionForm(forms.ModelForm):
    def clean_points(self):
        points = self.cleaned_data["points"]
        if points == 0:
            raise forms.ValidationError("Укажите ненулевое число баллов.")
        balance = User.objects.values_list("bonus_balance", flat=True).get(pk=self.instance.user_id)
        if balance + points < 0:
            raise forms.ValidationError(f"Нельзя списать больше, чем есть на балансе ({balance}).")
        return points


class BonusTransactionInline(admin.TabularInline):
    model = BonusTransaction
    form = BonusTransactionForm
    fields = ("created_at", "kind", "points", "comment", "booking")
    readonly_fields = ("created_at", "booking")
    extra = 0
    verbose_name = "корректировка баллов"
    verbose_name_plural = "Баллы: журнал и ручная корректировка"

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == "kind":
            kwargs["choices"] = [(BonusTransaction.Kind.ADJUST, BonusTransaction.Kind.ADJUST.label)]
        return super().formfield_for_choice_field(db_field, request, **kwargs)


class BookingInline(admin.TabularInline):
    model = Booking
    fields = ("starts_at", "location", "status", "total")
    readonly_fields = fields
    extra = 0
    show_change_link = True
    can_delete = False
    verbose_name_plural = "История броней"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = UserCreationForm
    ordering = ("-date_joined",)
    list_display = ("email", "name", "phone", "bonus_balance", "is_active", "date_joined")
    list_filter = ("is_active", "is_staff")
    search_fields = ("email", "name", "phone")
    readonly_fields = ("bonus_balance", "consent_at", "date_joined", "last_login")
    filter_horizontal = ("groups", "user_permissions")
    inlines = (BonusTransactionInline, BookingInline)

    fieldsets = (
        ("Клиент", {"fields": ("email", "name", "phone", "is_active")}),
        ("Бонусы и согласие", {"fields": ("bonus_balance", "consent_at")}),
        (
            "Доступ в админку",
            {
                "classes": ("collapse",),
                "description": "Нужно только сотрудникам. Клиенты входят по коду из письма, пароль им не нужен.",
                "fields": ("is_staff", "is_superuser", "groups", "user_permissions", "password"),
            },
        ),
        ("Даты", {"classes": ("collapse",), "fields": ("date_joined", "last_login")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "name", "phone", "usable_password", "password1", "password2"),
                "description": "Для клиента пароль не нужен: выберите «Пароль не нужен» в поле ниже.",
            },
        ),
    )
