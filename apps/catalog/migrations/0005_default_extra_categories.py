from django.db import migrations

TITLES = ("Услуги", "Свет", "Насадки и рефлекторы", "Фоны и холсты", "Прочее")


def create_categories(apps, schema_editor):
    ExtraCategory = apps.get_model("catalog", "ExtraCategory")
    if ExtraCategory.objects.exists():
        return
    ExtraCategory.objects.bulk_create(ExtraCategory(title=title, sort=index) for index, title in enumerate(TITLES, start=1))


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_extra_category'),
    ]

    operations = [
        migrations.RunPython(create_categories, migrations.RunPython.noop),
    ]
