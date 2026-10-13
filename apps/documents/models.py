from django.core.validators import FileExtensionValidator
from django.db import models


class Document(models.Model):
    slug = models.SlugField(
        "Код",
        unique=True,
        help_text=(
            "Латиницей, например offer или studio-rules. По этому коду фронтенд ищет документ, "
            "поэтому после публикации код лучше не менять."
        ),
    )
    title = models.CharField("Название", max_length=200, help_text="Так документ называется на сайте.")
    file = models.FileField(
        "PDF-файл",
        upload_to="documents/",
        validators=[FileExtensionValidator(["pdf"])],
        help_text="При замене файла старый удаляется автоматически.",
    )
    is_published = models.BooleanField("Опубликован", default=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "документ"
        verbose_name_plural = "документы"
        ordering = ("title",)

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.pk:
            old = Document.objects.filter(pk=self.pk).values_list("file", flat=True).first()
            if old and old != self.file.name:
                self.file.storage.delete(old)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        name, storage = self.file.name, self.file.storage
        result = super().delete(*args, **kwargs)
        if name:
            storage.delete(name)
        return result
