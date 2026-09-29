import math
from datetime import UTC, datetime
from pathlib import Path

from tower_bot.core.heartbeat import render_metrics, write_textfile

NOW = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)


def test_render() -> None:
    s = render_metrics(True, 0.123, NOW)
    assert "tower_bot_up 1\n" in s
    assert "tower_bot_gateway_latency_seconds 0.123\n" in s
    assert f"tower_bot_heartbeat_timestamp_seconds {int(NOW.timestamp())}\n" in s
    assert "# TYPE tower_bot_up gauge" in s


def test_render_down_and_bad_latency() -> None:
    s = render_metrics(False, math.inf, NOW)
    assert "tower_bot_up 0\n" in s and "latency" not in s
    assert "latency" not in render_metrics(True, None, NOW)


def test_write_atomic(tmp_path: Path) -> None:
    p = tmp_path / "tower_bot.prom"
    write_textfile(p, "a 1\n")
    write_textfile(p, "a 2\n")
    assert p.read_text() == "a 2\n"
    assert list(tmp_path.iterdir()) == [p]
