from django.db import models, transaction
from django.db.models import F


class BonusTransaction(models.Model):
    class Kind(models.TextChoices):
        ACCRUAL = "accrual", "Начисление кешбэка"
        SPEND = "spend", "Списание за бронь"
        REFUND = "refund", "Возврат баллов"
        REVERSAL = "reversal", "Сторно кешбэка при отмене"
        ADJUST = "adjust", "Корректировка администратором"

    user = models.ForeignKey("users.User", verbose_name="Клиент", on_delete=models.PROTECT, related_name="bonus_transactions")
    booking = models.ForeignKey(
        "bookings.Booking",
        verbose_name="Бронь",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="bonus_transactions",
    )
    kind = models.CharField("Тип", max_length=16, choices=Kind.choices)
    points = models.IntegerField(
        "Баллы",
        help_text="Положительное число начисляет баллы, отрицательное списывает. 1 балл = 1 ₽.",
    )
    comment = models.CharField("Комментарий", max_length=255, blank=True)
    created_at = models.DateTimeField("Дата", auto_now_add=True)

    class Meta:
        verbose_name = "операция с баллами"
        verbose_name_plural = "операции с баллами"
        ordering = ("-created_at", "-id")

    def __str__(self):
        return f"{self.get_kind_display()}: {self.points:+d}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Операции с баллами не редактируются: добавьте корректирующую.")
        from apps.users.models import User

        with transaction.atomic():
            super().save(*args, **kwargs)
            User.objects.filter(pk=self.user_id).update(bonus_balance=F("bonus_balance") + self.points)
