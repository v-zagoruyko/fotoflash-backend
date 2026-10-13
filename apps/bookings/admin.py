from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.utils.html import format_html, format_html_join

from apps.bonuses.models import BonusTransaction
from apps.catalog.models import Extra
from apps.core.errors import ApiError
from apps.payments.models import Payment

from . import services
from .exceptions import BookingError
from .models import Booking, BookingExtra


class AddBookingForm(forms.ModelForm):
    """Ручная бронь: создаётся теми же сервисами, что и на сайте (цена, слоты, допы, баллы)."""

    extras = forms.ModelMultipleChoiceField(
        label="Оборудование и услуги",
        queryset=Extra.objects.filter(is_active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Каждая позиция берётся на всё время брони. Количество часов можно поправить после создания.",
    )
    bonus_points = forms.IntegerField(
        label="Оплатить баллами",
        min_value=0,
        initial=0,
        required=False,
        help_text="Сколько баллов клиента списать. Лимит зависит от баланса и настроек студии.",
    )
    paid_manually = forms.BooleanField(
        label="Оплата уже получена",
        required=False,
        help_text="Отметьте, если клиент заплатил наличными или переводом: бронь сразу станет подтверждённой.",
    )

    class Meta:
        model = Booking
        fields = ("user", "location", "starts_at", "ends_at")

    def _order(self):
        data = self.cleaned_data
        return {
            "user": data["user"],
            "location": data["location"],
            "starts_at": data["starts_at"],
            "ends_at": data["ends_at"],
            "extras": [services.ExtraOrder(e.pk) for e in data.get("extras") or []],
            "bonus_points": data.get("bonus_points") or 0,
            "allow_past": True,
        }

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        try:
            services.quote_booking(**self._order())
        except BookingError as exc:
            raise forms.ValidationError(exc.message) from exc
        return cleaned

    def _post_clean(self):
        # Ограничения модели проверяют сервисы: форма не должна строить и валидировать черновик брони.
        pass

    def save(self, commit=True):
        booking = services.create_booking(**self._order())
        if self.cleaned_data.get("paid_manually"):
            booking = services.confirm_booking_manually(booking)
        return booking

    def save_m2m(self):
        pass


class LockedWhenCompletedMixin:
    """Состав завершённой брони не редактируется."""

    def _unlocked(self, obj):
        return obj is None or obj.status != Booking.Status.COMPLETED

    def has_add_permission(self, request, obj=None):
        return self._unlocked(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._unlocked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._unlocked(obj) and super().has_delete_permission(request, obj)


class BookingExtraInline(LockedWhenCompletedMixin, admin.TabularInline):
    model = BookingExtra
    extra = 0
    autocomplete_fields = ("extra",)
    fields = ("extra", "hours", "unit_price", "amount")


class PaymentInline(admin.TabularInline):
    model = Payment
    fields = ("created_at", "provider", "status", "amount", "payment_url")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class BookingBonusInline(admin.TabularInline):
    model = BonusTransaction
    fields = ("created_at", "kind", "points", "comment")
    readonly_fields = fields
    extra = 0
    can_delete = False
    verbose_name_plural = "Операции с баллами по этой брони"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    date_hierarchy = "starts_at"
    list_display = ("number", "starts_at", "ends_at", "location", "user", "status", "total", "bonus_spent")
    list_display_links = ("number", "starts_at")
    list_filter = ("status", "location", "starts_at")
    search_fields = ("user__email", "user__name", "user__phone")
    search_help_text = "Номер брони, email, имя или телефон клиента."
    autocomplete_fields = ("user",)
    list_select_related = ("user", "location")
    inlines = (BookingExtraInline, PaymentInline, BookingBonusInline)
    actions = ("confirm_paid_manually", "cancel_selected")
    add_form = AddBookingForm
    change_form_template = "admin/bookings/booking/change_form.html"

    fieldsets = (
        (
            "Бронь",
            {"fields": ("status", "user", "location", ("starts_at", "ends_at"), "expires_at", "comment")},
        ),
        (
            "Стоимость",
            {
                "description": (
                    "Редактирование здесь не пересчитывает цену и баллы: значения сохраняются как есть. "
                    "Для отмены с возвратом баллов используйте кнопку «Отменить бронь»."
                ),
                "fields": ("subtotal", "bonus_spent", "total"),
            },
        ),
        (
            "Служебная информация",
            {"classes": ("collapse",), "fields": ("id", "created_at", "cancelled_at", "reminder_sent_at", "price_breakdown_display")},
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "description": (
                    "Бронь создаётся так же, как на сайте: проверяются рабочие часы, занятость зала и смежных залов, "
                    "остатки оборудования, а цена считается автоматически."
                ),
                "fields": ("user", "location", ("starts_at", "ends_at"), "extras", "bonus_points", "paid_manually"),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        fields = ["id", "status", "created_at", "cancelled_at", "reminder_sent_at", "price_breakdown_display"]
        if obj is not None:
            fields.append("user")
        return fields

    @admin.display(description="Номер", ordering="id")
    def number(self, obj):
        return f"№{obj.pk}"

    @admin.display(description="Расчёт стоимости")
    def price_breakdown_display(self, obj):
        data = obj.price_breakdown or {}
        if not data:
            return "—"
        rows = [
            ("Часов", data.get("hours")),
            ("Базовая цена", f"{data.get('base')} ₽"),
            ("Доплата за поздние часы", f"{data.get('late')} ₽"),
            ("Доплата за выходные", f"{data.get('weekend')} ₽"),
            ("Итого за зал", f"{data.get('location')} ₽"),
        ]
        rows += [(f"{e['title']} ({e['hours']} ч)", f"{e['amount']} ₽") for e in data.get("extras", [])]
        return format_html("<table>{}</table>", format_html_join("", "<tr><td>{}</td><td>{}</td></tr>", rows))

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.status == Booking.Status.COMPLETED:
            return False
        return super().has_change_permission(request, obj)

    def get_search_results(self, request, queryset, search_term):
        found, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        number = search_term.strip().lstrip("#№")
        if number.isdigit():
            found = queryset.filter(Q(pk__in=found.values("pk")) | Q(pk=int(number)))
        return found, may_have_duplicates

    def response_change(self, request, obj):
        if "_confirm_manually" in request.POST:
            return self._apply(
                request, obj, services.confirm_booking_manually, "Бронь подтверждена, оплата отмечена как полученная вручную."
            )
        if "_cancel_booking" in request.POST:
            return self._apply(
                request,
                obj,
                lambda booking: services.cancel_booking(booking, by_staff=True),
                "Бронь отменена. Баллы возвращены, кешбэк снят, онлайн-оплата возвращена через банк.",
            )
        return super().response_change(request, obj)

    def _apply(self, request, obj, action, success_message):
        try:
            action(obj)
        except ApiError as exc:
            self.message_user(request, exc.message, messages.ERROR)
        else:
            self.message_user(request, success_message, messages.SUCCESS)
        return HttpResponseRedirect(request.path)

    def get_fieldsets(self, request, obj=None):
        return self.add_fieldsets if obj is None else super().get_fieldsets(request, obj)

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = self.add_form
        return super().get_form(request, obj, **kwargs)

    def get_inlines(self, request, obj=None):
        return [] if obj is None else super().get_inlines(request, obj)

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
        # При создании бронь уже сохранена формой через сервисы.

    @admin.action(description="Подтвердить: оплата получена вручную (наличные / перевод)")
    def confirm_paid_manually(self, request, queryset):
        done = 0
        for booking in queryset.filter(status=Booking.Status.PENDING):
            try:
                services.confirm_booking_manually(booking)
            except BookingError as exc:
                self.message_user(request, f"{booking}: {exc.message}", messages.ERROR)
                continue
            done += 1
        self.message_user(request, f"Подтверждено броней: {done}.", messages.SUCCESS if done else messages.WARNING)

    @admin.action(description="Отменить (вернуть баллы, снять кешбэк)")
    def cancel_selected(self, request, queryset):
        done = 0
        for booking in queryset:
            try:
                services.cancel_booking(booking, by_staff=True)
            except ApiError as exc:
                self.message_user(request, f"{booking}: {exc.message}", messages.ERROR)
                continue
            done += 1
            if booking.payments.filter(provider="manual", status="confirmed").exists():
                self.message_user(
                    request, f"{booking}: оплата была принята вручную, верните деньги клиенту самостоятельно.", messages.WARNING
                )
        self.message_user(
            request,
            f"Отменено броней: {done}. Онлайн-оплата по ним возвращена через банк.",
            messages.SUCCESS if done else messages.WARNING,
        )
