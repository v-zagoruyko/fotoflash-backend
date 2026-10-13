"""Переносит данные старого сайта (SQLite-дамп): клиентов с хешами паролей, баллы, брони, залы и оборудование.

Запускается один раз при первом деплое и безопасна при повторном запуске: уже перенесённое пропускается,
каталог (залы, допы) повторно не перезаписывается, чтобы не затереть правки менеджера.
Персональные данные в вывод не попадают, только счётчики.
"""

import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.hashers import identify_hasher
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.management.color import no_style
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.bonuses.models import BonusTransaction
from apps.bookings.models import Booking, BookingExtra
from apps.bookings.services import OVERLAP_CONSTRAINT
from apps.catalog.models import Extra, ExtraCategory, Location
from apps.payments.models import Payment
from apps.users.models import User

from .seed_demo import Command as SeedDemoCommand
from .seed_demo import slugify_ru

BALANCE_COMMENT = "Перенос баланса из старой системы"
OVERLAP_COMMENT = "Пересечение с другой бронью в старой системе: при переносе помечена отменённой (была оплачена, проверьте вручную)."


DEFAULT_CATEGORY = "Прочее"
# Старое поле `kind` → категория допа. Всё, чего здесь нет, попадает в «Прочее».
KIND_CATEGORIES = {
    "услуги студии": "Услуги",
    "источники света": "Свет",
    "светоформирующие насадки": "Насадки и рефлекторы",
    "фоны": "Фоны и холсты",
}


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def normalize_phone(raw: str | None) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10:
        return "+7" + digits
    return ""


def clean_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class Command(SeedDemoCommand):
    help = "Переносит клиентов, баллы, брони, залы и оборудование из дампа старого сайта (идемпотентно)"

    def add_arguments(self, parser):
        parser.add_argument("--legacy-db", required=True, help="Путь к db.sqlite3 старого сайта")
        parser.add_argument("--legacy-media", help="Путь к api/media старого сайта (нужен, если каталог переносится)")
        parser.add_argument("--dry-run", action="store_true", help="Выполнить всё в транзакции и откатить, показать отчёт")
        parser.add_argument("--keep-staff", action="store_true", help="Сохранить флаги is_staff/is_superuser старых админов")
        parser.add_argument("--include-frozen", action="store_true", help="Переносить баллы с замороженных карт")
        parser.add_argument("--update-catalog", action="store_true", help="Перезаписать залы и допы из дампа, даже если они уже есть")

    def handle(self, *args, **opts):
        db_path = Path(opts["legacy_db"])
        if not db_path.exists():
            raise CommandError(f"Нет файла {db_path}")
        self.media_src = Path(opts["legacy_media"]) if opts["legacy_media"] else None
        self.keep_staff = opts["keep_staff"]
        self.include_frozen = opts["include_frozen"]
        self.stats: Counter = Counter()
        self.tz = timezone.get_current_timezone()

        legacy = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        legacy.row_factory = sqlite3.Row

        with transaction.atomic():
            locations = self.load_locations(legacy, update=opts["update_catalog"])
            users = self.import_users(legacy)
            self.import_bookings(legacy, users, locations)
            self.assign_extra_categories(legacy)
            self.reset_sequences()
            self.report()
            if opts["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Dry-run: изменения откатены."))

    # --- каталог ----------------------------------------------------------------------------------

    def load_locations(self, legacy, *, update: bool) -> dict[int, Location]:
        """Залы: если каталог уже есть, только сопоставляем по slug, иначе переносим из дампа вместе с оборудованием."""
        if Location.objects.exists() and not update:
            by_slug = {loc.slug: loc for loc in Location.objects.all()}
            mapping = {}
            for row in legacy.execute("select id, title from api_area"):
                location = by_slug.get(slugify_ru(clean_title(row["title"])))
                if location:
                    mapping[row["id"]] = location
            self.stats["locations_matched"] = len(mapping)
            return mapping

        if self.media_src is None:
            raise CommandError("Каталог пуст: укажите --legacy-media, чтобы перенести залы и оборудование.")
        rules = self.import_rules(legacy)
        mapping = self.import_locations(legacy, rules)
        self.import_gallery(legacy, mapping)
        extras = self.import_extras(legacy)
        call_command("optimize_images", stdout=self.stdout)
        self.stats["locations_created"] = len(mapping)
        self.stats["extras_created"] = len(extras)
        return mapping

    def resolve_extra(self, legacy_stock) -> Extra:
        """Допа нет в каталоге (в новый его не переносили): заводим скрытый, чтобы сохранить историю брони."""
        title = clean_title(legacy_stock["title"])
        extra = Extra.objects.filter(title=title).first()
        if extra:
            return extra
        hourly = legacy_stock["rentCostPerHour"] or legacy_stock["rentCostPerDay"] // 10
        self.stats["extras_hidden_for_history"] += 1
        return Extra.objects.create(
            title=title,
            hour_price=Decimal(hourly),
            quantity=None if legacy_stock["category"] == "SERVICES" else max(1, legacy_stock["rentQty"]),
            is_active=False,
        )

    def assign_extra_categories(self, legacy) -> None:
        """Категории допам без категории: по старому `kind`, остальным «Прочее». Ручную раскладку не трогает."""
        categories: dict[str, ExtraCategory] = {}

        def category(title: str) -> ExtraCategory:
            if title not in categories:
                last = ExtraCategory.objects.order_by("-sort").values_list("sort", flat=True).first() or 0
                categories[title] = ExtraCategory.objects.get_or_create(title=title, defaults={"sort": last + 1})[0]
            return categories[title]

        for row in legacy.execute("select title, kind from api_stock"):
            title = KIND_CATEGORIES.get(clean_title(row["kind"] or "").lower(), DEFAULT_CATEGORY)
            updated = Extra.objects.filter(title=clean_title(row["title"]), category__isnull=True).update(category=category(title))
            self.stats["extras_categorized"] += updated
        rest = Extra.objects.filter(category__isnull=True).update(category=category(DEFAULT_CATEGORY))
        self.stats["extras_categorized_default"] += rest

    # --- клиенты ----------------------------------------------------------------------------------

    def import_users(self, legacy) -> dict[int, User]:
        """Старые клиенты → новые. Совпадающие по email без учёта регистра сливаются в одного."""
        bookings_count = dict(legacy.execute("select user_id, count(*) from api_booking group by 1").fetchall())
        rows = legacy.execute(
            """select u.*, p.phone, c.balance, c.isFreezed
               from api_user u
               left join api_userprofile p on p.user_id = u.id
               left join api_bonuscard c on c.id = p.card_id
               order by u.id"""
        ).fetchall()
        groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            email = (row["email"] or "").strip().lower()
            if email:
                groups[email].append(row)
            else:
                self.stats["users_skipped_no_email"] += 1

        users: dict[int, User] = {}
        for email, group in groups.items():
            main = max(group, key=lambda r: (bookings_count.get(r["id"], 0), r["last_login"] or "", -r["id"]))
            user = User.objects.filter(email=email).first()
            if user:
                self.stats["users_existing"] += 1
            else:
                user = self.create_user(email, main, group)
                self.add_balance(user, group)
            for row in group:
                users[row["id"]] = user
            if len(group) > 1:
                self.stats["users_merged_duplicates"] += len(group) - 1
        return users

    def create_user(self, email: str, main: sqlite3.Row, group: list[sqlite3.Row]) -> User:
        password = main["password"]
        try:
            identify_hasher(password)
        except ValueError:
            password = ""
            self.stats["users_unusable_hash"] += 1
        is_staff = self.keep_staff and any(r["is_staff"] or r["is_superuser"] for r in group)
        if not self.keep_staff and any(r["is_staff"] or r["is_superuser"] for r in group):
            self.stats["staff_downgraded_to_client"] += 1
        logins = [r["last_login"] for r in group if r["last_login"]]
        user = User(
            email=email,
            name=f"{main['first_name']} {main['last_name']}".strip()[:150],
            phone=normalize_phone(main["phone"]),
            is_active=bool(main["is_active"]),
            is_staff=is_staff,
            is_superuser=is_staff and any(r["is_superuser"] for r in group),
            date_joined=parse_dt(min(r["date_joined"] for r in group)),
            last_login=parse_dt(max(logins)) if logins else None,
        )
        if password:
            user.password = password
        else:
            user.set_unusable_password()
        user.save()
        self.stats["users_created"] += 1
        return user

    def add_balance(self, user: User, group: list[sqlite3.Row]) -> None:
        points = 0
        for row in group:
            balance = row["balance"] or 0
            if balance <= 0:
                if balance < 0:
                    self.stats["balance_negative_skipped"] += 1
                continue
            if row["is_staff"] or row["is_superuser"]:
                self.stats["balance_staff_account_skipped"] += 1
                self.stats["balance_staff_points_skipped"] += balance
                continue
            if row["isFreezed"] and not self.include_frozen:
                self.stats["balance_frozen_skipped"] += 1
                self.stats["balance_frozen_points_skipped"] += balance
                continue
            points += balance
        if points:
            BonusTransaction.objects.create(user=user, kind=BonusTransaction.Kind.ADJUST, points=points, comment=BALANCE_COMMENT)
            self.stats["balance_users"] += 1
            self.stats["balance_points"] += points

    # --- брони ------------------------------------------------------------------------------------

    def window(self, row: sqlite3.Row) -> tuple[datetime, datetime]:
        day = datetime.strptime(row["date"], "%Y-%m-%d").date()
        start_time = time.fromisoformat(row["timeStart"])
        starts_at = timezone.make_aware(datetime.combine(day, start_time), self.tz)
        if row["timeEnd"] is None:
            self.stats["bookings_end_missing_plus_1h"] += 1
            return starts_at, starts_at + timedelta(hours=1)
        end_time = time.fromisoformat(row["timeEnd"])
        ends_at = timezone.make_aware(datetime.combine(day, end_time), self.tz)
        if ends_at <= starts_at:  # 00:00 или ночная бронь: окончание на следующие сутки
            ends_at = timezone.make_aware(datetime.combine(day + timedelta(days=1), end_time), self.tz)
        return starts_at, ends_at

    def import_bookings(self, legacy, users: dict[int, User], locations: dict[int, Location]) -> None:
        comments: dict[int, list[str]] = defaultdict(list)
        for row in legacy.execute("select booking_id, created_at, comment from api_commentstracker order by created_at, id"):
            text = (row["comment"] or "").strip()
            if text:
                stamp = parse_dt(row["created_at"])
                comments[row["booking_id"]].append(f"{timezone.localtime(stamp):%d.%m.%Y}: {text}" if stamp else text)

        stock = {r["id"]: r for r in legacy.execute("select * from api_stock")}
        extras_by_booking: dict[int, set[int]] = defaultdict(set)
        for table in ("api_booking_stock", "api_booking_services"):
            for row in legacy.execute(f"select booking_id, stock_id from {table}"):  # noqa: S608 (имя таблицы из константы)
                extras_by_booking[row["booking_id"]].add(row["stock_id"])

        today_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        extra_cache: dict[int, Extra] = {}

        for row in legacy.execute("select * from api_booking order by id"):
            user, location = users.get(row["user_id"]), locations.get(row["area_id"])
            if not user or not location:
                self.stats["bookings_skipped_no_user_or_location"] += 1
                continue
            starts_at, ends_at = self.window(row)
            if Booking.objects.filter(user=user, location=location, starts_at=starts_at, ends_at=ends_at).exists():
                self.stats["bookings_duplicate_or_existing"] += 1
                continue

            if not row["isPayed"]:
                status = Booking.Status.EXPIRED
            elif ends_at <= today_start:
                status = Booking.Status.COMPLETED
            else:
                status = Booking.Status.CONFIRMED

            note = "\n".join(comments.get(row["id"], []))
            fields = {
                "user": user,
                "location": location,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "subtotal": Decimal(row["price"]),
                "bonus_spent": row["bonus"],
                "total": Decimal(row["cash"]),
                "price_breakdown": {"legacy": True, "legacy_booking_id": row["id"]},
                "comment": note,
            }
            if not Booking.objects.filter(pk=row["id"]).exists():
                fields["id"] = row["id"]
            booking = self.save_booking(fields, status)

            created_at = parse_dt(row["createdAt"])
            if created_at:
                Booking.objects.filter(pk=booking.pk).update(created_at=created_at)

            hours = max(1, math.ceil((ends_at - starts_at) / timedelta(hours=1)))
            lines = []
            for stock_id in sorted(extras_by_booking.get(row["id"], ())):
                if stock_id not in stock:
                    continue
                extra = extra_cache.get(stock_id) or extra_cache.setdefault(stock_id, self.resolve_extra(stock[stock_id]))
                lines.append(
                    BookingExtra(booking=booking, extra=extra, hours=hours, unit_price=extra.hour_price, amount=extra.hour_price * hours)
                )
            BookingExtra.objects.bulk_create(lines)

            if booking.status in (Booking.Status.CONFIRMED, Booking.Status.COMPLETED) and booking.total:
                Payment.objects.create(
                    booking=booking, provider=Payment.Provider.MANUAL, amount=booking.total, status=Payment.Status.CONFIRMED
                )

    def save_booking(self, fields: dict, status: str) -> Booking:
        try:
            with transaction.atomic():
                booking = Booking(status=status, **fields)
                booking.save(force_insert=True)
                self.stats[f"bookings_{status}"] += 1
                return booking
        except IntegrityError as exc:
            if OVERLAP_CONSTRAINT not in str(exc):
                raise
        fields = {**fields, "comment": f"{OVERLAP_COMMENT}\n{fields['comment']}".strip()}
        booking = Booking(status=Booking.Status.CANCELLED, cancelled_at=timezone.now(), **fields)
        booking.save(force_insert=True)
        self.stats["bookings_overlap_cancelled"] += 1
        return booking

    # --- служебное --------------------------------------------------------------------------------

    def reset_sequences(self) -> None:
        with connection.cursor() as cursor:
            for sql in connection.ops.sequence_reset_sql(no_style(), [Booking]):
                cursor.execute(sql)

    def report(self) -> None:
        self.stdout.write("Отчёт о переносе:")
        for key in sorted(self.stats):
            self.stdout.write(f"  {key}: {self.stats[key]}")
