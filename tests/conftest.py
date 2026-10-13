from datetime import UTC, datetime, time, timedelta

import pytest

from apps.catalog.models import Location
from apps.users.models import User


@pytest.fixture
def client_user(db):
    return User.objects.create_user("client@example.com", name="Клиент")


@pytest.fixture
def location(db):
    return Location.objects.create(
        title="Лофт",
        slug="loft",
        hour_price=1500,
        late_from=time(22, 0),
        late_surcharge=300,
        weekend_surcharge=500,
        cover="x.jpg",
    )


@pytest.fixture
def slot():
    start = datetime(2030, 1, 7, 10, tzinfo=UTC)
    return start, start + timedelta(hours=2)


@pytest.fixture(autouse=True)
def isolated_infra(settings, monkeypatch):
    """Кеш в памяти (коды входа, троттлинг) и синхронное выполнение Celery-задач без брокера."""
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    from celery.app.task import Task
    from django.core.cache import cache

    cache.clear()
    monkeypatch.setattr(Task, "delay", lambda self, *args, **kwargs: self.apply(args, kwargs))
    monkeypatch.setattr(Task, "apply_async", lambda self, args=None, kwargs=None, **options: self.apply(args or (), kwargs or {}))
