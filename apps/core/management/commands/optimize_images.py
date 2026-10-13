from django.apps import apps
from django.core.management.base import BaseCommand
from PIL import Image

from apps.core.fields import OptimizedImageField
from apps.core.images import optimize_image, webp_name


class Command(BaseCommand):
    help = "Сжимает и конвертирует в WebP уже загруженные изображения (идемпотентно)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Только показать, что будет обработано")

    def handle(self, *args, dry_run=False, **opts):
        processed = skipped = missing = 0
        saved_bytes = 0
        for model in apps.get_models():
            fields = [f for f in model._meta.get_fields() if isinstance(f, OptimizedImageField)]
            for field in fields:
                for obj in model.objects.exclude(**{field.name: ""}).exclude(**{f"{field.name}__isnull": True}):
                    file = getattr(obj, field.name)
                    if not file.storage.exists(file.name):
                        missing += 1
                        continue
                    with file.storage.open(file.name) as source:
                        size_before = source.size
                        with Image.open(source) as image:
                            is_webp = image.format == "WEBP"
                            fits = max(image.size) <= field.max_side
                    if is_webp and fits:
                        skipped += 1
                        continue
                    processed += 1
                    if dry_run:
                        continue
                    old_name = file.name
                    with file.storage.open(old_name) as source:
                        content = optimize_image(source, max_side=field.max_side, quality=field.quality)
                    new_name = file.storage.save(f"{file.field.upload_to}{webp_name(old_name.rsplit('/', 1)[-1])}", content)
                    model.objects.filter(pk=obj.pk).update(**{field.name: new_name})
                    file.storage.delete(old_name)
                    saved_bytes += size_before - content.size
        mode = "найдено для обработки" if dry_run else "обработано"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: {processed}, уже оптимальны: {skipped}, файл не найден: {missing}, экономия: {saved_bytes / 1024 / 1024:.1f} МБ"
            )
        )
