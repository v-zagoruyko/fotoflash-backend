from django.db import migrations

# ОГРНИП на старом сайте не указан, заполняется в админке.
LEGAL_DETAILS = {
    "legal_name": "ИП Гладаренко Лилия Васильевна",
    "inn": "720403866940",
    "legal_address": "625046, Тюменская обл., г. Тюмень, ул. Зоологическая, д. 27",
}


def fill_legal_details(apps, schema_editor):
    StudioSettings = apps.get_model("core", "StudioSettings")
    studio, _ = StudioSettings.objects.get_or_create(pk=1)
    for field, value in LEGAL_DETAILS.items():
        if not getattr(studio, field):
            setattr(studio, field, value)
    studio.save()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_booking_horizon_and_legal_details'),
    ]

    operations = [
        migrations.RunPython(fill_legal_details, migrations.RunPython.noop),
    ]
