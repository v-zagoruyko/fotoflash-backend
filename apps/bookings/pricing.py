"""Расчёт цены и проверка временного окна. Чистые функции без записи в БД."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.catalog.models import Extra, Location

from .exceptions import InvalidTimeRange

HOUR = timedelta(hours=1)
WEEKEND_DAYS = (5, 6)


@dataclass(frozen=True)
class ExtraLine:
    extra: Extra
    hours: int
    unit_price: Decimal
    amount: Decimal


@dataclass(frozen=True)
class PriceQuote:
    hours: int
    location_amount: Decimal
    extra_lines: tuple[ExtraLine, ...]
    subtotal: Decimal
    breakdown: dict


def iter_hours(starts_at: datetime, ends_at: datetime) -> Iterator[datetime]:
    """Начала каждого часа брони в локальном часовом поясе."""
    current = starts_at
    while current < ends_at:
        yield timezone.localtime(current)
        current += HOUR


def hour_price(location: Location, hour_start: datetime) -> tuple[Decimal, Decimal, Decimal]:
    """Возвращает (база, доплата за поздний час, доплата за выходной) для часа, начинающегося в `hour_start`."""
    local = timezone.localtime(hour_start)
    t = local.time()
    is_late = location.late_from is not None and (t >= location.late_from or t < location.open_time)
    is_weekend = local.weekday() in WEEKEND_DAYS
    return (
        location.hour_price,
        location.late_surcharge if is_late else Decimal("0"),
        location.weekend_surcharge if is_weekend else Decimal("0"),
    )


def working_window(location: Location, day: date) -> tuple[datetime, datetime]:
    """Рабочее окно зала для даты: если закрытие не позже открытия, оно приходится на следующие сутки."""
    tz = timezone.get_current_timezone()
    closes_day = day if location.close_time > location.open_time else day + timedelta(days=1)
    opens = timezone.make_aware(datetime.combine(day, location.open_time), tz)
    closes = timezone.make_aware(datetime.combine(closes_day, location.close_time), tz)
    return opens, closes


def validate_window(location: Location, starts_at: datetime, ends_at: datetime, now: datetime, *, allow_past: bool = False) -> int:
    """Проверяет границы брони и возвращает её длительность в часах."""
    if timezone.is_naive(starts_at) or timezone.is_naive(ends_at):
        raise InvalidTimeRange("Укажите время вместе с часовым поясом.")
    for value in (starts_at, ends_at):
        local = timezone.localtime(value)
        if local.minute or local.second or local.microsecond:
            raise InvalidTimeRange("Бронировать можно только на целые часы.")
    if ends_at <= starts_at:
        raise InvalidTimeRange("Время окончания должно быть позже начала.")
    if starts_at < now and not allow_past:
        raise InvalidTimeRange("Нельзя забронировать время в прошлом.")

    hours = int((ends_at - starts_at) / HOUR)
    if hours < location.min_hours:
        raise InvalidTimeRange(f"Минимальная длительность брони — {location.min_hours} ч.")

    start_day = timezone.localtime(starts_at).date()
    for day in (start_day, start_day - timedelta(days=1)):
        opens, closes = working_window(location, day)
        if opens <= starts_at and ends_at <= closes:
            return hours
    raise InvalidTimeRange(f"Зал работает с {location.open_time:%H:%M} до {location.close_time:%H:%M}. Выберите время в этом интервале.")


def calculate_price(
    location: Location,
    starts_at: datetime,
    ends_at: datetime,
    extras: Sequence[tuple[Extra, int]] = (),
) -> PriceQuote:
    """Стоимость зала по часам плюс допы. `extras` — пары (позиция, количество часов)."""
    base = late = weekend = Decimal("0")
    hours = 0
    for hour_start in iter_hours(starts_at, ends_at):
        hour_base, hour_late, hour_weekend = hour_price(location, hour_start)
        base, late, weekend = base + hour_base, late + hour_late, weekend + hour_weekend
        hours += 1
    location_amount = base + late + weekend

    lines = tuple(ExtraLine(extra=e, hours=h, unit_price=e.hour_price, amount=e.hour_price * h) for e, h in extras)
    subtotal = location_amount + sum((line.amount for line in lines), Decimal("0"))
    breakdown = {
        "hours": hours,
        "base": str(base),
        "late": str(late),
        "weekend": str(weekend),
        "location": str(location_amount),
        "extras": [
            {"extra_id": line.extra.pk, "title": line.extra.title, "hours": line.hours, "amount": str(line.amount)} for line in lines
        ],
        "subtotal": str(subtotal),
    }
    return PriceQuote(hours=hours, location_amount=location_amount, extra_lines=lines, subtotal=subtotal, breakdown=breakdown)
