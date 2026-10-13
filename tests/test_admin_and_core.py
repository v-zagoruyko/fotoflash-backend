import pytest
from django.urls import reverse

from apps.core.models import StudioSettings
from apps.users.models import User


def test_studio_settings_is_singleton(db):
    first = StudioSettings.load()
    StudioSettings.objects.create(hold_minutes=5)
    assert StudioSettings.objects.count() == 1
    assert StudioSettings.load().pk == first.pk


@pytest.fixture
def admin_client(client, db):
    admin = User.objects.create_superuser("admin@example.com", "pass12345")
    client.force_login(admin)
    return client


@pytest.mark.parametrize(
    "name",
    [
        "users_user",
        "documents_document",
        "catalog_location",
        "catalog_extra",
        "bookings_booking",
        "payments_payment",
        "bonuses_bonustransaction",
    ],
)
def test_admin_pages_render(admin_client, name, client_user, location):
    assert admin_client.get(reverse(f"admin:{name}_changelist")).status_code == 200
    assert admin_client.get(reverse(f"admin:{name}_add")).status_code in (200, 403)


def test_admin_change_pages_render(admin_client, client_user, location):
    assert admin_client.get(reverse("admin:users_user_change", args=[client_user.pk])).status_code == 200
    assert admin_client.get(reverse("admin:catalog_location_change", args=[location.pk])).status_code == 200
    assert admin_client.get(reverse("admin:core_studiosettings_changelist"), follow=True).status_code == 200


def test_healthz(client, db):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_studio_contacts_are_public(db, client):
    StudioSettings.objects.filter(pk=1).delete()
    studio = StudioSettings.load()
    studio.address = "г. Тюмень, ул. Зоологическая, 31"
    studio.contact_phone = "+7 922 267-00-55"
    studio.contact_email = "info@fotoflash.studio"
    studio.map_url = "https://yandex.ru/maps/-/CLabc"
    studio.legal_name = "ИП Иванов Иван Иванович"
    studio.inn = "720000000000"
    studio.legal_address = "625000, г. Тюмень, ул. Тестовая, д. 1"
    studio.save()

    response = client.get("/api/v1/studio/")

    assert response.status_code == 200
    assert response.json() == {
        "address": "г. Тюмень, ул. Зоологическая, 31",
        "phone": "+7 922 267-00-55",
        "email": "info@fotoflash.studio",
        "map_url": "https://yandex.ru/maps/-/CLabc",
        "booking_horizon_days": 92,
        "legal_name": "ИП Иванов Иван Иванович",
        "inn": "720000000000",
        "ogrnip": "",
        "legal_address": "625000, г. Тюмень, ул. Тестовая, д. 1",
    }
