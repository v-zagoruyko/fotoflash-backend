from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from PIL import Image

from apps.catalog.models import Extra, Location, LocationImage


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def upload(size=(4000, 3000), fmt="JPEG", mode="RGB", name="photo.jpg", **save_kwargs):
    buffer = BytesIO()
    Image.new(mode, size, "red").save(buffer, format=fmt, **save_kwargs)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def stored_image(field_file):
    with field_file.storage.open(field_file.name) as f:
        return Image.open(BytesIO(f.read()))


def make_location(cover):
    return Location.objects.create(title="Лофт", slug="loft", hour_price=1000, cover=cover)


def test_large_photo_is_resized_and_converted_to_webp(db):
    original = upload((4000, 3000), quality=100)
    location = make_location(original)

    assert location.cover.name.endswith(".webp")
    image = stored_image(location.cover)
    assert image.format == "WEBP"
    assert max(image.size) == 2000
    assert image.size == (2000, 1500)
    assert location.cover.size < original.size / 3


def test_small_photo_is_not_upscaled(db):
    location = make_location(upload((800, 600)))
    assert stored_image(location.cover).size == (800, 600)


def test_extra_cover_uses_smaller_limit(db):
    extra = Extra.objects.create(title="Свет", hour_price=100, cover=upload((3000, 2000)))
    assert max(stored_image(extra.cover).size) == 1000


def test_png_transparency_is_preserved(db):
    buffer = BytesIO()
    Image.new("RGBA", (500, 500), (255, 0, 0, 100)).save(buffer, format="PNG")
    location = make_location(SimpleUploadedFile("logo.png", buffer.getvalue(), content_type="image/png"))
    image = stored_image(location.cover)
    assert image.format == "WEBP"
    assert "A" in image.getbands()


def test_exif_orientation_is_applied(db):
    buffer = BytesIO()
    source = Image.new("RGB", (400, 200), "blue")
    exif = Image.Exif()
    exif[0x0112] = 6  # повернуть на 90° по часовой
    source.save(buffer, format="JPEG", exif=exif)
    location = make_location(SimpleUploadedFile("rot.jpg", buffer.getvalue(), content_type="image/jpeg"))
    assert stored_image(location.cover).size == (200, 400)


def test_saving_again_does_not_reprocess_file(db):
    location = make_location(upload())
    name = location.cover.name
    location.title = "Новое название"
    location.save()
    assert location.cover.name == name


def test_optimize_command_converts_existing_files_idempotently(db, settings):
    big = upload((3500, 2500), quality=100)
    location = make_location(cover="locations/cover/legacy.jpg")
    path = settings.MEDIA_ROOT / "locations/cover/legacy.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(big.read())
    LocationImage.objects.create(location=location, image="locations/gallery/missing.jpg")

    call_command("optimize_images", "--dry-run")
    assert path.exists()

    call_command("optimize_images")
    location.refresh_from_db()
    assert location.cover.name.endswith(".webp")
    assert not path.exists()
    assert max(stored_image(location.cover).size) == 2000

    name = location.cover.name
    call_command("optimize_images")
    location.refresh_from_db()
    assert location.cover.name == name
