"""Заполняет dev-базу демо-данными.

Каталог (залы, смежные залы, оборудование и услуги, фото, правила) берётся из дампа старого сайта.
Клиенты, брони и баллы генерируются: персональные данные из дампа не копируются.
"""

import html
import random
import re
import shutil
import sqlite3
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags
from PIL import Image, ImageDraw, ImageFont

from apps.bonuses.models import BonusTransaction
from apps.bookings import services
from apps.bookings.exceptions import BookingError
from apps.bookings.models import Booking
from apps.catalog.models import Extra, Location, LocationImage
from apps.documents.models import Document
from apps.payments.models import Payment
from apps.users.models import User

DEMO_DOMAIN = "demo.fotoflash.local"
FIRST_NAMES = ["Анна", "Мария", "Елена", "Ольга", "Ирина", "Дмитрий", "Алексей", "Сергей", "Иван", "Павел", "Наталья", "Екатерина"]
LAST_NAMES = ["Иванов", "Петров", "Смирнов", "Кузнецов", "Попов", "Соколов", "Лебедев", "Козлов", "Новиков", "Морозов"]

TRANSLIT = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        [
            "a",
            "b",
            "v",
            "g",
            "d",
            "e",
            "e",
            "zh",
            "z",
            "i",
            "y",
            "k",
            "l",
            "m",
            "n",
            "o",
            "p",
            "r",
            "s",
            "t",
            "u",
            "f",
            "h",
            "c",
            "ch",
            "sh",
            "sch",
            "",
            "y",
            "",
            "e",
            "yu",
            "ya",
        ],
        strict=True,
    )
)


def slugify_ru(value: str) -> str:
    value = "".join(TRANSLIT.get(ch, ch) for ch in value.lower())
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def clean_text(value: str | None) -> str:
    text = html.unescape(strip_tags(value or ""))
    return re.sub(r"[ \t]+", " ", text).strip()


class Command(BaseCommand):
    help = "Заполняет базу демо-данными (залы и оборудование из старого дампа, клиенты и брони сгенерированы)"

    def add_arguments(self, parser):
        parser.add_argument("--legacy-db", required=True, help="Путь к db.sqlite3 старого сайта")
        parser.add_argument("--legacy-media", required=True, help="Путь к api/media старого сайта")
        parser.add_argument("--clients", type=int, default=25)
        parser.add_argument("--bookings", type=int, default=60)
        parser.add_argument("--admin-password", default="admin", help="Пароль admin@example.com (создаётся только при DEBUG)")
        parser.add_argument("--reset-demo", action="store_true", help="Сначала удалить демо-клиентов и их брони")

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("Демо-данные можно заливать только при DEBUG=True.")
        db_path = Path(opts["legacy_db"])
        if not db_path.exists():
            raise CommandError(f"Нет файла {db_path}")
        self.media_src = Path(opts["legacy_media"])
        self.rng = random.Random(42)

        legacy = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        legacy.row_factory = sqlite3.Row

        with transaction.atomic():
            if opts["reset_demo"]:
                self.reset_demo()
            rules = self.import_rules(legacy)
            locations = self.import_locations(legacy, rules)
            self.import_gallery(legacy, locations)
            extras = self.import_extras(legacy)
            self.create_staff_user(opts["admin_password"])

        call_command("optimize_images", stdout=self.stdout)
        self.seed_clients_and_bookings(opts["clients"], opts["bookings"])
        self.stdout.write(self.style.SUCCESS(f"Готово: залов {len(locations)}, допов {len(extras)}."))

    # --- каталог ----------------------------------------------------------------------------------

    def copy_media(self, relative: str | None) -> str:
        """Копирует файл из старой media в MEDIA_ROOT. Возвращает относительный путь или пустую строку."""
        if not relative:
            return ""
        source = self.media_src / relative
        if not source.is_file():
            return ""
        target = Path(settings.MEDIA_ROOT) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)
        return relative

    def find_rules_pdf(self, legacy) -> str:
        """PDF из карточки зала, а если файла нет в архиве media, самый свежий «Правила…» из папки docs."""
        for row in legacy.execute("select rules from api_area where rules is not null and rules != '' order by id"):
            if (self.media_src / row["rules"]).is_file():
                return row["rules"]
        pdfs = list((self.media_src / "docs").glob("*.pdf"))
        candidates = [p for p in pdfs if "правила_студии" in p.name.lower()] or [p for p in pdfs if "правила" in p.name.lower()]
        if candidates:
            return "docs/" + max(candidates, key=lambda p: p.stat().st_mtime).name
        return ""

    def placeholder_cover(self, slug: str, title: str) -> str:
        relative = f"locations/cover/placeholder-{slug}.jpg"
        target = Path(settings.MEDIA_ROOT) / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            hue = sum(map(ord, slug)) % 360
            image = Image.new("RGB", (1200, 800))
            image.paste(f"hsl({hue}, 35%, 40%)", (0, 0, 1200, 800))
            draw = ImageDraw.Draw(image)
            draw.text((60, 60), slug.upper(), fill="white", font=ImageFont.load_default(size=64))
            image.save(target, quality=85)
        return relative

    def import_rules(self, legacy) -> Document | None:
        name = self.copy_media(self.find_rules_pdf(legacy))
        if not name:
            self.stdout.write(self.style.WARNING("PDF с правилами не найден, пропускаю."))
            return None
        document, _ = Document.objects.update_or_create(
            slug="studio-rules", defaults={"title": "Правила студии", "file": name, "is_published": True}
        )
        return document

    def import_locations(self, legacy, rules) -> dict[int, Location]:
        gallery_first = {
            r["relation_id"]: r["image"]
            for r in legacy.execute("select relation_id, min(image) as image from api_areaimagegallery group by relation_id")
        }
        locations: dict[int, Location] = {}
        for i, row in enumerate(legacy.execute("select * from api_area order by id")):
            title = re.sub(r"\s+", " ", row["title"]).strip()
            slug = slugify_ru(title)
            cover = self.copy_media(row["cover"]) or self.copy_media(gallery_first.get(row["id"])) or self.placeholder_cover(slug, title)
            location, _ = Location.objects.update_or_create(
                slug=slug,
                defaults={
                    "title": title,
                    "description": clean_text(row["text"]),
                    "cover": cover,
                    "rules_document": rules,
                    "hour_price": Decimal(row["rentCost"]),
                    "late_from": time(22, 0) if row["rentCostLateTime"] else None,
                    "late_surcharge": Decimal(row["rentCostLateTime"]),
                    "weekend_surcharge": Decimal(row["rentCostWeekend"]),
                    "sort": i,
                    "is_active": True,
                },
            )
            locations[row["id"]] = location

        for row in legacy.execute("select from_area_id, to_area_id from api_area_twin where from_area_id != to_area_id"):
            a, b = locations.get(row["from_area_id"]), locations.get(row["to_area_id"])
            if a and b:
                a.conflicts_with.add(b)  # связь симметричная
        return locations

    def import_gallery(self, legacy, locations) -> None:
        for row in legacy.execute("select relation_id, image from api_areaimagegallery order by id"):
            location = locations.get(row["relation_id"])
            name = self.copy_media(row["image"])
            if location and name:
                LocationImage.objects.get_or_create(location=location, image=name)

    def import_extras(self, legacy) -> list[Extra]:
        """В старом каталоге оборудование чаще сдаётся посуточно. В новой модели цена только за час,
        поэтому для демо она берётся как стоимость суток / 10, округлённая до 50 ₽ (реальные цены — отдельное решение)."""
        extras = []
        for i, row in enumerate(legacy.execute("select * from api_stock order by category, kind, title")):
            if row["rentQty"] <= 0 and row["category"] == "STOCK":
                continue
            hourly = row["rentCostPerHour"]
            if not hourly and row["rentCostPerDay"]:
                hourly = max(50, round(row["rentCostPerDay"] / 10 / 50) * 50)
            if not hourly:
                continue
            is_service = row["category"] == "SERVICES"
            extra, _ = Extra.objects.update_or_create(
                title=re.sub(r"\s+", " ", row["title"]).strip(),
                defaults={
                    "description": clean_text(row["text"]),
                    "cover": self.copy_media(row["cover"]),
                    "hour_price": Decimal(hourly),
                    "quantity": None if is_service else max(1, row["rentQty"]),
                    "is_active": True,
                    "sort": i,
                },
            )
            extras.append(extra)
        return extras

    def create_staff_user(self, password: str) -> None:
        admin, created = User.objects.get_or_create(
            email="admin@example.com", defaults={"name": "Администратор", "is_staff": True, "is_superuser": True}
        )
        if created:
            admin.set_password(password)
            admin.save()
            self.stdout.write(f"Суперпользователь: admin@example.com / {password}")

    # --- клиенты и брони --------------------------------------------------------------------------

    def reset_demo(self) -> None:
        users = User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}")
        bookings = Booking.objects.filter(user__in=users)
        Payment.objects.filter(booking__in=bookings).delete()
        BonusTransaction.objects.filter(user__in=users).delete()
        bookings.delete()
        users.delete()

    def seed_clients_and_bookings(self, clients: int, bookings: int) -> None:
        users = []
        for n in range(1, clients + 1):
            user, created = User.objects.get_or_create(
                email=f"client{n:02d}@{DEMO_DOMAIN}",
                defaults={
                    "name": f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}",
                    "phone": f"+7900{self.rng.randint(1000000, 9999999)}",
                    "consent_at": timezone.now(),
                },
            )
            if created and self.rng.random() < 0.3:
                BonusTransaction.objects.create(
                    user=user, kind=BonusTransaction.Kind.ADJUST, points=self.rng.choice([300, 500, 1000]), comment="Демо-баллы"
                )
                user.refresh_from_db()
            users.append(user)

        if Booking.objects.filter(user__in=users).exists():
            self.stdout.write("Демо-брони уже есть, пропускаю (используйте --reset-demo).")
            return

        locations = list(Location.objects.filter(is_active=True))
        extras = list(Extra.objects.filter(is_active=True))
        today = timezone.localdate()
        tz = timezone.get_current_timezone()
        created = skipped = 0

        for _ in range(bookings):
            day = today + timedelta(days=self.rng.randint(-25, 14))
            duration = self.rng.randint(1, 4)
            start_hour = self.rng.randint(9, 24 - duration)
            starts_at = timezone.make_aware(datetime.combine(day, time(start_hour)), tz)
            ends_at = starts_at + timedelta(hours=duration)
            is_past = starts_at < timezone.now()
            now = starts_at - timedelta(days=2) if is_past else timezone.now()

            user = self.rng.choice(users)
            orders = [services.ExtraOrder(e.pk) for e in self.rng.sample(extras, k=self.rng.choice([0, 0, 1, 2]))]
            try:
                location = self.rng.choice(locations)
                max_bonus = 0
                if user.bonus_balance:
                    quote = services.quote_booking(
                        location=location, starts_at=starts_at, ends_at=ends_at, extras=orders, user=user, now=now
                    )
                    max_bonus = min(quote.max_bonus_points, 300)
                booking = services.create_booking(
                    user=user,
                    location=location,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    extras=orders,
                    bonus_points=max_bonus,
                    now=now,
                )
                user.refresh_from_db()
                roll = self.rng.random()
                if is_past or roll < 0.55:
                    services.confirm_booking_manually(booking, now=now)
                if roll > 0.85:
                    services.cancel_booking(booking, by_staff=True, now=now)
                created += 1
            except BookingError:
                skipped += 1
        self.stdout.write(f"Брони: создано {created}, пропущено из-за пересечений {skipped}.")
