from django.db import models


class StudioSettings(models.Model):
    hold_minutes = models.PositiveSmallIntegerField(
        "Время на оплату, мин",
        default=30,
        help_text="Сколько минут слот удерживается за неоплаченной бронью. По истечении бронь отменяется автоматически.",
    )
    cancel_before_hours = models.PositiveSmallIntegerField(
        "Отмена клиентом: не позднее чем за, ч",
        default=24,
        help_text="Клиент может сам отменить оплаченную бронь не позднее чем за столько часов до начала. Позже — только администратор.",
    )
    reminder_before_hours = models.PositiveSmallIntegerField(
        "Напоминание о брони: за, ч",
        default=24,
        help_text="За сколько часов до начала клиенту уйдёт письмо-напоминание.",
    )
    cashback_percent = models.PositiveSmallIntegerField(
        "Кешбэк, %",
        default=3,
        help_text="Процент от оплаченной суммы, который возвращается клиенту баллами (1 балл = 1 ₽).",
    )
    cashback_on_bonus_bookings = models.BooleanField(
        "Кешбэк, если часть оплачена баллами",
        default=True,
        help_text=(
            "Включено: кешбэк считается от денежной части брони. "
            "Выключено: если клиент платил баллами хотя бы частично, кешбэк за эту бронь не начисляется."
        ),
    )
    max_bonus_share_percent = models.PositiveSmallIntegerField(
        "Максимум оплаты баллами, %",
        default=50,
        help_text="Какую долю стоимости брони можно оплатить баллами.",
    )

    address = models.CharField(
        "Адрес студии",
        max_length=255,
        blank=True,
        help_text="Показывается в письмах клиентам: как добраться.",
    )
    contact_phone = models.CharField(
        "Телефон студии",
        max_length=32,
        blank=True,
        help_text="Показывается в письмах клиентам.",
    )

    class Meta:
        verbose_name = "Настройки студии"
        verbose_name_plural = "Настройки студии"

    def __str__(self):
        return "Настройки студии"

    def save(self, *args, **kwargs):
        self.pk = 1
        kwargs["force_insert"] = False
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls) -> "StudioSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
