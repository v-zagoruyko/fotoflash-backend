from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

WEBP_QUALITY = 82


def optimize_image(source, *, max_side: int, quality: int = WEBP_QUALITY) -> ContentFile:
    """Приводит фото к WebP: учитывает EXIF-поворот, уменьшает по длинной стороне (но не увеличивает)."""
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        has_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
        image = image.convert("RGBA" if has_alpha else "RGB")
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="WEBP", quality=quality, method=6)
    return ContentFile(buffer.getvalue())


def webp_name(name: str) -> str:
    return f"{Path(name).stem}.webp"
