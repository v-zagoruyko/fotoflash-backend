from datetime import time

from django.db import models

from apps.core.fields import OptimizedImageField


class Location(models.Model):
    title = models.CharField("Название", max_length=128)
    slug = models.SlugField(
        "Код для адреса страницы",
        unique=True,
        help_text="Латиницей, попадает в ссылку на зал, например /halls/loft. После публикации лучше не менять.",
    )
    description = models.TextField("Описание", blank=True)
    cover = OptimizedImageField(
        "Главное фото",
        upload_to="locations/cover/",
        max_side=2000,
        help_text="Любой размер и формат: при загрузке фото сожмётся до 2000 px по длинной стороне и сохранится в WebP.",
    )
    rules_document = models.ForeignKey(
        "documents.Document",
        verbose_name="Правила зала",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="locations",
        help_text="PDF с правилами. Уходит клиенту в письме после подтверждения брони.",
    )
    is_active = models.BooleanField(
        "Доступен для бронирования",
        default=True,
        help_text="Снимите галочку, чтобы скрыть зал с сайта. Существующие брони сохранятся.",
    )
    sort = models.PositiveIntegerField("Порядок", default=0, editable=False, db_index=True)

    hour_price = models.DecimalField(
        "Цена за час, ₽",
        max_digits=8,
        decimal_places=2,
        help_text="Базовая стоимость одного часа в будни в обычное время.",
    )
    late_from = models.TimeField(
        "Поздний тариф с",
        null=True,
        blank=True,
        help_text="С какого времени час считается «поздним», например 22:00. Оставьте пустым, если доплаты нет.",
    )
    late_surcharge = models.DecimalField(
        "Доплата за поздний час, ₽",
        max_digits=8,
        decimal_places=2,
        default=0,
        help_text="Добавляется к цене каждого часа, который начинается во время позднего тарифа.",
    )
    weekend_surcharge = models.DecimalField(
        "Доплата за выходной час, ₽",
        max_digits=8,
        decimal_places=2,
        default=0,
        help_text="Добавляется к цене каждого часа в субботу и воскресенье.",
    )

    open_time = models.TimeField("Открывается", default=time(9, 0))
    close_time = models.TimeField(
        "Закрывается",
        default=time(0, 0),
        help_text="00:00 означает «до полуночи».",
    )
    min_hours = models.PositiveSmallIntegerField(
        "Минимум часов в брони",
        default=1,
        help_text="Бронь короче этого значения оформить нельзя.",
    )

    conflicts_with = models.ManyToManyField(
        "self",
        verbose_name="Смежные залы",
        blank=True,
        help_text=(
            "Залы, которые нельзя занять одновременно с этим (например, общая зона или проходной зал). "
            "Связь двусторонняя: достаточно указать её в одном из залов."
        ),
    )

    class Meta:
        verbose_name = "зал"
        verbose_name_plural = "залы"
        ordering = ("sort", "title")

    def __str__(self):
        return self.title


class LocationImage(models.Model):
    location = models.ForeignKey(Location, verbose_name="Зал", on_delete=models.CASCADE, related_name="images")
    image = OptimizedImageField("Фото", upload_to="locations/gallery/", max_side=2000)
    sort = models.PositiveIntegerField("Порядок", default=0, db_index=True)

    class Meta:
        verbose_name = "фото зала"
        verbose_name_plural = "галерея"
        ordering = ("sort", "id")

    def __str__(self):
        return f"{self.location}: фото {self.pk}"


class Extra(models.Model):
    title = models.CharField("Название", max_length=128)
    description = models.TextField("Описание", blank=True)
    cover = OptimizedImageField("Фото", upload_to="extras/", max_side=1000, null=True, blank=True)
    hour_price = models.DecimalField("Цена за час, ₽", max_digits=8, decimal_places=2)
    quantity = models.PositiveSmallIntegerField(
        "Количество",
        null=True,
        blank=True,
        help_text="Сколько единиц есть в наличии. Если пусто, ограничения нет (подходит для услуг).",
    )
    is_active = models.BooleanField(
        "Доступно для заказа",
        default=True,
        help_text="Снимите галочку, чтобы скрыть позицию с сайта.",
    )
    sort = models.PositiveIntegerField("Порядок", default=0, editable=False, db_index=True)

    class Meta:
        verbose_name = "оборудование и услуга"
        verbose_name_plural = "оборудование и услуги"
        ordering = ("sort", "title")

    def __str__(self):
        return self.title
