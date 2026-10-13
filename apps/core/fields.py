from django.core.exceptions import ValidationError
from django.db import models

from .images import WEBP_QUALITY, optimize_image, webp_name

MAX_UPLOAD_MB = 20


def validate_upload_size(file):
    if file.size > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"Файл слишком большой: максимум {MAX_UPLOAD_MB} МБ.")


class OptimizedImageField(models.ImageField):
    """Изображение, которое при загрузке конвертируется в WebP и уменьшается до `max_side` пикселей по длинной стороне."""

    def __init__(self, *args, max_side: int = 2000, quality: int = WEBP_QUALITY, **kwargs):
        self.max_side = max_side
        self.quality = quality
        kwargs.setdefault("validators", [validate_upload_size])
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["max_side"] = self.max_side
        kwargs["quality"] = self.quality
        kwargs.pop("validators", None)
        return name, path, args, kwargs

    def pre_save(self, model_instance, add):
        file = getattr(model_instance, self.attname)
        if file and not file._committed:
            file.file = optimize_image(file.file, max_side=self.max_side, quality=self.quality)
            file.name = webp_name(file.name)
        return super().pre_save(model_instance, add)
