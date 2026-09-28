from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from tower_bot.gamenight.when import (
    WhenError,
    default_start,
    fmt_when,
    parse_many,
    parse_when,
)

TZ = ZoneInfo("America/New_York")
# Monday 2026-09-28 17:00 ET
NOW = datetime(2026, 9, 28, 17, 0, tzinfo=TZ)


def local(dt: datetime) -> tuple[int, int, int, int, int]:
    d = dt.astimezone(TZ)
    return (d.year, d.month, d.day, d.hour, d.minute)


def test_default_is_next_wednesday_9pm() -> None:
    assert local(default_start(NOW, TZ)) == (2026, 9, 30, 21, 0)


def test_default_on_wednesday_before_and_after_9pm() -> None:
    wed_before = datetime(2026, 9, 30, 20, 59, tzinfo=TZ)
    wed_after = datetime(2026, 9, 30, 21, 0, tzinfo=TZ)
    assert local(default_start(wed_before, TZ)) == (2026, 9, 30, 21, 0)
    assert local(default_start(wed_after, TZ)) == (2026, 10, 7, 21, 0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, (2026, 9, 30, 21, 0)),
        ("", (2026, 9, 30, 21, 0)),
        ("fri 8pm", (2026, 10, 2, 20, 0)),
        ("Friday 8:30 PM", (2026, 10, 2, 20, 30)),
        ("mon 9pm", (2026, 9, 28, 21, 0)),  # today is Mon 17:00, 9pm still ahead -> today
        ("at 9pm", (2026, 9, 28, 21, 0)),
        ("tonight", (2026, 9, 28, 21, 0)),
        ("tomorrow 10pm", (2026, 9, 29, 22, 0)),
        ("10/14 9pm", (2026, 10, 14, 21, 0)),
        ("10/14", (2026, 10, 14, 21, 0)),
        ("1/5 9pm", (2027, 1, 5, 21, 0)),  # past month -> next year
        ("10/14/2026 21:00", (2026, 10, 14, 21, 0)),
        ("9", (2026, 9, 28, 21, 0)),  # bare hour 1-11 means PM, today
        ("wed at 9pm", (2026, 9, 30, 21, 0)),
        ("WED  9PM", (2026, 9, 30, 21, 0)),
    ],
)
def test_parse(text: str | None, expected: tuple[int, int, int, int, int]) -> None:
    got = parse_when(text, NOW, TZ)
    assert local(got) == expected
    assert got.tzinfo is not None


def test_weekday_today_already_passed_goes_next_week() -> None:
    late = datetime(2026, 9, 28, 22, 0, tzinfo=TZ)
    assert local(parse_when("mon 9pm", late, TZ)) == (2026, 10, 5, 21, 0)


@pytest.mark.parametrize("text", ["5pm", "tonight 4pm", "9/1/2026 9pm", "1/1/2020"])
def test_past_is_rejected(text: str) -> None:
    with pytest.raises(WhenError):
        parse_when(text, NOW, TZ)


@pytest.mark.parametrize("text", ["someday", "13pm", "25:00", "2/30 9pm", "9:75pm", "fri fri"])
def test_garbage_is_rejected(text: str) -> None:
    with pytest.raises(WhenError):
        parse_when(text, NOW, TZ)


def test_dst_boundary() -> None:
    # DST ends 2026-11-01. 9pm EDT on 10/28 = 01:00Z; 9pm EST on 11/4 = 02:00Z next day.
    assert parse_when("10/28 9pm", NOW, TZ).astimezone(UTC).hour == 1
    assert parse_when("11/4 9pm", NOW, TZ).astimezone(UTC).hour == 2


def test_parse_many() -> None:
    got = parse_many("wed 9pm, thu 9pm; fri 8pm", NOW, TZ)
    assert [local(d)[2] for d in got] == [30, 1, 2]
    with pytest.raises(WhenError):
        parse_many("wed 9pm, nonsense", NOW, TZ)


def test_fmt_when() -> None:
    assert fmt_when(datetime(2026, 10, 7, 21, 0, tzinfo=TZ), TZ) == "Wed Oct 7, 9:00 PM ET"
    assert fmt_when(datetime(2026, 10, 7, 12, 5, tzinfo=TZ), TZ) == "Wed Oct 7, 12:05 PM ET"
