import sqlite3
from datetime import timedelta

import pytest
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.utils import timezone

from apps.bonuses.models import BonusTransaction
from apps.bookings.models import Booking
from apps.catalog.models import Extra, ExtraCategory, Location
from apps.core.management.commands.seed_demo import slugify_ru
from apps.payments.models import Payment
from apps.users.models import User

SCHEMA = """
create table api_user (id integer primary key, password text, last_login text, is_superuser bool, first_name text, last_name text,
    is_staff bool, is_active bool, date_joined text, email text, username text);
create table api_userprofile (id integer primary key, phone text, dob text, passport text, snils text, headshot text,
    user_id integer, card_id integer);
create table api_bonuscard (id integer primary key, type text, balance integer, isFreezed bool, hours integer);
create table api_area (id integer primary key, title text, text text, rentCost integer, rentCostLateTime integer,
    rentCostWeekend integer, cover text, rentCostFullNight integer, rentCostHalfNight integer, rules text);
create table api_area_twin (id integer primary key, from_area_id integer, to_area_id integer);
create table api_booking (id integer primary key, date text, timeStart text, timeEnd text, price integer, area_id integer,
    photograph_id integer, user_id integer, isPayed bool, bonus integer, cash integer, paymentUrl text, createdAt text, isCounted bool);
create table api_booking_services (id integer primary key, booking_id integer, stock_id integer);
create table api_booking_stock (id integer primary key, booking_id integer, stock_id integer);
create table api_stock (id integer primary key, kind text, title text, text text, qty integer, cost integer, cover text,
    rentCostPerDay integer, rentCostPerHour integer, rentQty integer, category text);
create table api_commentstracker (id integer primary key, created_at text, booking_id integer, comment text);
"""

OLD_HASH = make_password("old-password")


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "db.sqlite3"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    users = [
        # id, email, first, last, staff, active, login, joined, phone, balance, frozen
        (1, "Ann@Example.com", "Анна", "Иванова", 0, 1, "2026-01-01 10:00:00", "2020-01-01 10:00:00", "89991234567", 500, 0),
        (2, "ann@example.com", "Анна", "Дубль", 0, 1, None, "2019-05-05 10:00:00", None, 200, 0),
        (3, "boss@example.com", "Босс", "Админ", 1, 1, "2026-02-01 10:00:00", "2019-01-01 10:00:00", "79990000000", 1000000, 0),
        (4, "frozen@example.com", "Фриз", "Карта", 0, 1, None, "2021-01-01 10:00:00", "123", 700, 1),
        (5, "neg@example.com", "Минус", "Баланс", 0, 0, None, "2021-01-01 10:00:00", None, -300, 0),
    ]
    for uid, email, first, last, staff, active, login, joined, phone, balance, frozen in users:
        con.execute(
            "insert into api_user values (?,?,?,?,?,?,?,?,?,?,?)",
            (uid, OLD_HASH, login, staff, first, last, staff, active, joined, email, None),
        )
        con.execute("insert into api_bonuscard values (?,?,?,?,?)", (uid, "CLASSIC", balance, frozen, 0))
        con.execute("insert into api_userprofile values (?,?,?,?,?,?,?,?)", (uid, phone, None, "1234567890", None, None, uid, uid))
    con.execute("insert into api_area values (1,'Студия A','',3000,0,0,'',0,0,null)")
    con.execute("insert into api_stock values (1,'k','Проектор','',1,0,'',0,100,1,'STOCK')")
    con.execute("insert into api_stock values (2,'k','Снят с проката','',1,0,'',0,200,0,'STOCK')")

    future = (timezone.localdate() + timedelta(days=10)).isoformat()
    bookings = [
        # id, date, start, end, price, user, paid, bonus, cash
        (10, "2024-03-01", "10:00:00", "12:00:00", 6000, 1, 1, 0, 6000),  # прошла, оплачена
        (11, future, "10:00:00", "11:00:00", 3000, 2, 1, 100, 2900),  # будущая, аккаунт-дубль
        (12, "2024-03-02", "10:00:00", "11:00:00", 3000, 4, 0, 0, 3000),  # не оплачена
        (13, "2024-03-01", "11:00:00", "13:00:00", 6000, 4, 1, 0, 6000),  # пересекается с №10
        (14, "2019-12-07", "12:00:00", None, 1700, 1, 1, 0, 1700),  # нет окончания
        (15, "2024-03-05", "22:00:00", "09:00:00", 9000, 1, 1, 0, 9000),  # через ночь
        (16, "2024-03-06", "22:00:00", "00:00:00", 6000, 1, 1, 0, 6000),  # до полуночи
    ]
    for bid, day, start, end, price, uid, paid, bonus, cash in bookings:
        con.execute(
            "insert into api_booking values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bid, day, start, end, price, 1, None, uid, paid, bonus, cash, "", "2024-02-01 09:00:00", paid),
        )
    con.execute("insert into api_booking_stock values (1,10,1)")
    con.execute("insert into api_booking_services values (1,10,2)")
    con.execute("insert into api_commentstracker values (1,'2024-02-02 10:00:00',10,'Перенос с 24.02')")
    con.commit()
    con.close()
    return path


@pytest.fixture
def catalog(db):
    location = Location.objects.create(title="Студия A", slug=slugify_ru("Студия A"), hour_price=3000, cover="x.jpg")
    extra = Extra.objects.create(title="Проектор", hour_price=100)
    return location, extra


def run(path, *args):
    call_command("import_legacy", "--legacy-db", str(path), *args)


def test_users_are_merged_and_keep_password_hash(legacy_db, catalog):
    run(legacy_db)

    assert User.objects.count() == 4  # дубль Ann слит, остальные на месте
    ann = User.objects.get(email="ann@example.com")
    assert ann.check_password("old-password")
    assert ann.name == "Анна Иванова"
    assert ann.phone == "+79991234567"
    assert ann.consent_at is None
    assert ann.bookings.count() == 5  # брони обоих аккаунтов
    assert User.objects.get(email="neg@example.com").is_active is False
    assert User.objects.get(email="frozen@example.com").phone == ""


def test_staff_is_downgraded_and_balances_follow_rules(legacy_db, catalog):
    run(legacy_db)

    boss = User.objects.get(email="boss@example.com")
    assert not boss.is_staff and not boss.is_superuser
    assert boss.bonus_balance == 0  # баланс служебной карты не переносится
    assert User.objects.get(email="ann@example.com").bonus_balance == 700  # 500 + 200
    assert User.objects.get(email="frozen@example.com").bonus_balance == 0
    assert User.objects.get(email="neg@example.com").bonus_balance == 0
    assert BonusTransaction.objects.filter(user__email="ann@example.com").count() == 1


def test_keep_staff_and_include_frozen_flags(legacy_db, catalog):
    run(legacy_db, "--keep-staff", "--include-frozen")

    boss = User.objects.get(email="boss@example.com")
    assert boss.is_staff and boss.is_superuser
    assert boss.bonus_balance == 0
    assert User.objects.get(email="frozen@example.com").bonus_balance == 700


def test_booking_statuses_times_and_payments(legacy_db, catalog):
    run(legacy_db)
    tz = timezone.get_current_timezone()

    assert Booking.objects.get(pk=10).status == Booking.Status.COMPLETED
    assert Booking.objects.get(pk=11).status == Booking.Status.CONFIRMED
    assert Booking.objects.get(pk=11).bonus_spent == 100
    assert Booking.objects.get(pk=12).status == Booking.Status.EXPIRED
    overlapped = Booking.objects.get(pk=13)
    assert overlapped.status == Booking.Status.CANCELLED
    assert "Пересечение" in overlapped.comment

    no_end = Booking.objects.get(pk=14)
    assert no_end.ends_at - no_end.starts_at == timedelta(hours=1)
    night = Booking.objects.get(pk=15)
    assert timezone.localtime(night.ends_at, tz).day == 6 and timezone.localtime(night.ends_at, tz).hour == 9
    midnight = Booking.objects.get(pk=16)
    assert midnight.ends_at - midnight.starts_at == timedelta(hours=2)

    assert Booking.objects.get(pk=10).comment.endswith("Перенос с 24.02")
    assert Payment.objects.filter(booking_id=10, provider=Payment.Provider.MANUAL).exists()
    assert not Payment.objects.filter(booking_id=12).exists()


def test_extras_are_linked_and_unknown_ones_hidden(legacy_db, catalog):
    run(legacy_db)

    lines = Booking.objects.get(pk=10).extras.select_related("extra")
    assert {(line.extra.title, line.extra.is_active, line.hours) for line in lines} == {
        ("Проектор", True, 2),
        ("Снят с проката", False, 2),
    }


def test_extras_get_categories_by_kind_and_default_to_other(legacy_db, catalog):
    con = sqlite3.connect(legacy_db)
    con.execute("update api_stock set kind = 'ИСТОЧНИКИ СВЕТА' where id = 1")
    con.commit()
    con.close()
    manual = ExtraCategory.objects.create(title="Ручная")
    Extra.objects.create(title="Не из старой базы", hour_price=10)
    Extra.objects.create(title="Уже разложен", hour_price=10, category=manual)

    run(legacy_db)

    by_title = {e.title: e.category.title for e in Extra.objects.select_related("category")}
    assert by_title["Проектор"] == "Свет"
    assert by_title["Снят с проката"] == "Прочее"  # kind не из списка
    assert by_title["Не из старой базы"] == "Прочее"
    assert by_title["Уже разложен"] == "Ручная"
    assert not Extra.objects.filter(category__isnull=True).exists()


def test_import_is_idempotent_and_dry_run_rolls_back(legacy_db, catalog):
    run(legacy_db, "--dry-run")
    assert User.objects.count() == 0 and Booking.objects.count() == 0

    run(legacy_db)
    snapshot = (User.objects.count(), Booking.objects.count(), BonusTransaction.objects.count(), Payment.objects.count())
    run(legacy_db)

    assert (User.objects.count(), Booking.objects.count(), BonusTransaction.objects.count(), Payment.objects.count()) == snapshot
    # следующая бронь получает номер больше перенесённых
    new = Booking.objects.create(
        user=User.objects.first(),
        location=catalog[0],
        starts_at=timezone.now() + timedelta(days=60),
        ends_at=timezone.now() + timedelta(days=60, hours=1),
        subtotal=1,
        total=1,
    )
    assert new.pk > 16
