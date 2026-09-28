from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_WEEKDAY = 2  # Wednesday
DEFAULT_HOUR = 21
HELP = (
    "Try things like `fri 8pm`, `10/14 9pm`, `tomorrow 9:30pm`, "
    "or leave it empty for next Wednesday 9 PM."
)

WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "weds": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}
_RE = re.compile(
    r"^(?:(?P<day>[a-z]+|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s*)?"
    r"(?:(?:at\s+|@\s*)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?)?$"
)


class WhenError(ValueError):
    pass


def _at(d: date, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)


def default_start(now: datetime, tz: ZoneInfo) -> datetime:
    today = now.astimezone(tz).date()
    d = today + timedelta(days=(DEFAULT_WEEKDAY - today.weekday()) % 7)
    cand = _at(d, DEFAULT_HOUR, 0, tz)
    if cand <= now:
        cand = _at(d + timedelta(days=7), DEFAULT_HOUR, 0, tz)
    return cand


def _time(h: str, m: str | None, ap: str | None) -> tuple[int, int]:
    hour, minute = int(h), int(m or 0)
    if minute > 59:
        raise WhenError(f"Bad time. {HELP}")
    if ap:
        if not 1 <= hour <= 12:
            raise WhenError(f"Bad time. {HELP}")
        hour = hour % 12 + (12 if ap == "pm" else 0)
    else:
        if hour > 23:
            raise WhenError(f"Bad time. {HELP}")
        if 1 <= hour <= 11:  # a bare "9" means 9 PM: game nights are evenings
            hour += 12
    return hour, minute


def parse_when(text: str | None, now: datetime, tz: ZoneInfo) -> datetime:
    if text is None or not text.strip():
        return default_start(now, tz)
    s = re.sub(r"^at\s+", "", " ".join(text.strip().lower().split()))
    mt = _RE.match(s)
    if not mt or (mt["day"] is None and mt["h"] is None):
        raise WhenError(f"I couldn't read `{text}`. {HELP}")
    hour, minute = _time(mt["h"], mt["m"], mt["ap"]) if mt["h"] else (DEFAULT_HOUR, 0)
    today = now.astimezone(tz).date()
    day = mt["day"]
    if day is None or day in ("today", "tonight"):
        d = today
    elif day in ("tomorrow", "tmrw"):
        d = today + timedelta(days=1)
    elif day in WEEKDAYS:
        d = today + timedelta(days=(WEEKDAYS[day] - today.weekday()) % 7)
        if _at(d, hour, minute, tz) <= now:
            d += timedelta(days=7)
    elif "/" in day:
        parts = [int(p) for p in day.split("/")]
        year = parts[2] if len(parts) == 3 else today.year
        if year < 100:
            year += 2000
        try:
            d = date(year, parts[0], parts[1])
        except ValueError as e:
            raise WhenError(f"`{day}` isn't a real date.") from e
        if len(parts) == 2 and _at(d, hour, minute, tz) <= now:
            d = date(year + 1, parts[0], parts[1])
    else:
        raise WhenError(f"I couldn't read `{text}`. {HELP}")
    result = _at(d, hour, minute, tz)
    if result <= now:
        raise WhenError("That time is already past.")
    return result


def parse_many(text: str, now: datetime, tz: ZoneInfo) -> list[datetime]:
    parts = [p for p in re.split(r"[;,]", text) if p.strip()]
    return [parse_when(p, now, tz) for p in parts]


def fmt_when(dt: datetime, tz: ZoneInfo) -> str:
    d = dt.astimezone(tz)
    hour12 = d.hour % 12 or 12
    ampm = "AM" if d.hour < 12 else "PM"
    return f"{d:%a %b} {d.day}, {hour12}:{d:%M} {ampm} ET"
