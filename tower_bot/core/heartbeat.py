from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path


def render_metrics(up: bool, latency: float | None, now: datetime) -> str:
    lines = [
        "# HELP tower_bot_up 1 when the Discord gateway connection is ready.",
        "# TYPE tower_bot_up gauge",
        f"tower_bot_up {1 if up else 0}",
        "# HELP tower_bot_heartbeat_timestamp_seconds Last time the bot wrote this file.",
        "# TYPE tower_bot_heartbeat_timestamp_seconds gauge",
        f"tower_bot_heartbeat_timestamp_seconds {int(now.timestamp())}",
    ]
    if latency is not None and math.isfinite(latency):
        lines += [
            "# HELP tower_bot_gateway_latency_seconds Discord gateway heartbeat latency.",
            "# TYPE tower_bot_gateway_latency_seconds gauge",
            f"tower_bot_gateway_latency_seconds {round(latency, 3)}",
        ]
    return "\n".join(lines) + "\n"


def write_textfile(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)
