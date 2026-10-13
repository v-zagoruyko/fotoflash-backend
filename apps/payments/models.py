from django.db import models


class Payment(models.Model):
    class Provider(models.TextChoices):
        TBANK = "tbank", "Т-Банк"
        MANUAL = "manual", "Вручную (наличные / перевод)"
        FAKE = "fake", "Тестовый (только dev)"

    class Status(models.TextChoices):
        NEW = "new", "Создан"
        PENDING = "pending", "Ожидает оплаты"
        CONFIRMED = "confirmed", "Оплачен"
        REJECTED = "rejected", "Отклонён"
        EXPIRED = "expired", "Истёк"
        REFUNDED = "refunded", "Возвращён"

    booking = models.ForeignKey("bookings.Booking", verbose_name="Бронь", on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField("Способ", max_length=16, choices=Provider.choices, default=Provider.TBANK)
    provider_payment_id = models.CharField("ID платежа в банке", max_length=64, blank=True, db_index=True)
    amount = models.DecimalField("Сумма, ₽", max_digits=9, decimal_places=2)
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.NEW, db_index=True)
    payment_url = models.URLField("Ссылка на оплату", max_length=512, blank=True)
    receipt = models.JSONField("Данные чека", default=dict, blank=True, help_text="Что передано банку для фискального чека.")
    raw = models.JSONField("Ответы банка", default=dict, blank=True, help_text="Технические данные для разбора спорных платежей.")
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "платёж"
        verbose_name_plural = "платежи"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.amount} ₽ · {self.get_status_display()}"
