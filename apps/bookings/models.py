from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db import DEFAULT_DB_ALIAS, models
from django.db.models import F, Func, Q
from django.utils import timezone
from django_fsm import FSMField, FSMModelMixin, transition


class TstzRange(Func):
    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class BookingOverlapConstraint(ExclusionConstraint):
    """Статус брони не редактируется формой, но нужен для условия ограничения: берём его из экземпляра."""

    def validate(self, model, instance, exclude=None, using=DEFAULT_DB_ALIAS):
        exclude = set(exclude or ()) - {"status"}
        return super().validate(model, instance, exclude=exclude, using=using)


class Booking(FSMModelMixin, models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает оплаты"
        CONFIRMED = "confirmed", "Подтверждена"
        CANCELLED = "cancelled", "Отменена"
        EXPIRED = "expired", "Не оплачена вовремя"
        COMPLETED = "completed", "Завершена"

    id = models.BigAutoField("Номер", primary_key=True)
    user = models.ForeignKey("users.User", verbose_name="Клиент", on_delete=models.PROTECT, related_name="bookings")
    location = models.ForeignKey("catalog.Location", verbose_name="Зал", on_delete=models.PROTECT, related_name="bookings")
    starts_at = models.DateTimeField("Начало")
    ends_at = models.DateTimeField("Конец", help_text="Время окончания, не включительно: бронь на 10:00–12:00 занимает два часа.")
    status = FSMField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        protected=True,
        db_index=True,
        help_text="Меняется только действиями: «Подтвердить», «Отменить» и автоматически (оплата, истечение времени, завершение).",
    )
    expires_at = models.DateTimeField(
        "Оплатить до",
        null=True,
        blank=True,
        help_text="До этого момента слот удерживается за неоплаченной бронью.",
    )

    subtotal = models.DecimalField(
        "Стоимость, ₽", max_digits=9, decimal_places=2, help_text="Зал плюс оборудование и услуги, до списания баллов."
    )
    bonus_spent = models.PositiveIntegerField("Оплачено баллами", default=0)
    total = models.DecimalField("К оплате, ₽", max_digits=9, decimal_places=2, help_text="Стоимость за вычетом баллов.")
    price_breakdown = models.JSONField(
        "Расчёт стоимости",
        default=dict,
        blank=True,
        help_text="Из чего сложилась цена: базовая, поздние часы, выходные. Заполняется автоматически.",
    )

    created_at = models.DateTimeField("Создана", auto_now_add=True)
    cancelled_at = models.DateTimeField("Отменена", null=True, blank=True)
    reminder_sent_at = models.DateTimeField(
        "Напоминание отправлено",
        null=True,
        blank=True,
        editable=False,
        help_text="Когда клиенту ушло письмо-напоминание. Пусто, если ещё не отправлялось.",
    )
    comment = models.TextField("Комментарий менеджера", blank=True, help_text="Виден только сотрудникам, клиент его не видит.")

    class Meta:
        verbose_name = "бронь"
        verbose_name_plural = "брони"
        ordering = ("-starts_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")),
                name="booking_ends_after_start",
                violation_error_message="Время окончания должно быть позже начала.",
            ),
            BookingOverlapConstraint(
                name="booking_no_overlap_in_location",
                expressions=[
                    (TstzRange("starts_at", "ends_at"), RangeOperators.OVERLAPS),
                    ("location", RangeOperators.EQUAL),
                ],
                condition=Q(status__in=["pending", "confirmed", "completed"]),
                violation_error_message="В это время зал уже занят.",
            ),
        ]

    def __str__(self):
        return f"Бронь №{self.pk} · {self.location} · {timezone.localtime(self.starts_at):%d.%m.%Y %H:%M}"

    @transition(field=status, source=Status.PENDING, target=Status.CONFIRMED)
    def confirm(self):
        self.expires_at = None

    @transition(field=status, source=[Status.PENDING, Status.CONFIRMED], target=Status.CANCELLED)
    def cancel(self, now):
        self.cancelled_at = now
        self.expires_at = None

    @transition(field=status, source=Status.PENDING, target=Status.EXPIRED)
    def expire(self):
        pass

    @transition(field=status, source=Status.CONFIRMED, target=Status.COMPLETED)
    def complete(self):
        pass


class BookingExtra(models.Model):
    booking = models.ForeignKey(Booking, verbose_name="Бронь", on_delete=models.CASCADE, related_name="extras")
    extra = models.ForeignKey("catalog.Extra", verbose_name="Позиция", on_delete=models.PROTECT, related_name="booking_items")
    hours = models.PositiveSmallIntegerField("Часов", default=1)
    unit_price = models.DecimalField(
        "Цена за час, ₽",
        max_digits=8,
        decimal_places=2,
        help_text="Цена на момент брони. Изменение каталога на неё не влияет.",
    )
    amount = models.DecimalField("Сумма, ₽", max_digits=9, decimal_places=2)

    class Meta:
        verbose_name = "оборудование и услуга в брони"
        verbose_name_plural = "оборудование и услуги в брони"

    def __str__(self):
        return f"{self.extra} × {self.hours} ч"
