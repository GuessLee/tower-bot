from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    discord_token: str
    database_url: str
    db_password: str | None
    github_token: str
    github_repo: str
    feedback_labels: tuple[str, ...]
    guild_id: int
    night_channel_id: int
    library_channel_id: int
    log_channel_id: int
    admin_role_id: int
    tz: ZoneInfo
    textfile_path: Path | None


def _get(env: Mapping[str, str], key: str) -> str | None:
    path = env.get(f"{key}_FILE")
    if path:
        return Path(path).read_text().strip()
    val = env.get(key)
    return val.strip() if val else None


def load_config(env: Mapping[str, str]) -> Config:
    required = [
        "DISCORD_TOKEN",
        "DATABASE_URL",
        "GITHUB_TOKEN",
        "GUILD_ID",
        "NIGHT_CHANNEL_ID",
        "LIBRARY_CHANNEL_ID",
        "LOG_CHANNEL_ID",
        "ADMIN_ROLE_ID",
    ]
    vals = {k: _get(env, k) for k in required}
    missing = [k for k, v in vals.items() if not v]
    if missing:
        raise ConfigError(f"missing config: {', '.join(missing)}")

    def as_int(key: str) -> int:
        try:
            return int(vals[key] or "")
        except ValueError as e:
            raise ConfigError(f"{key} must be an integer") from e

    labels = tuple(
        s.strip()
        for s in (_get(env, "FEEDBACK_LABELS") or "feedback,classify-me").split(",")
        if s.strip()
    )
    textfile = _get(env, "TEXTFILE_PATH")
    return Config(
        discord_token=vals["DISCORD_TOKEN"] or "",
        database_url=vals["DATABASE_URL"] or "",
        db_password=_get(env, "DB_PASSWORD"),
        github_token=vals["GITHUB_TOKEN"] or "",
        github_repo=_get(env, "GITHUB_REPO") or "GuessLee/unraid",
        feedback_labels=labels,
        guild_id=as_int("GUILD_ID"),
        night_channel_id=as_int("NIGHT_CHANNEL_ID"),
        library_channel_id=as_int("LIBRARY_CHANNEL_ID"),
        log_channel_id=as_int("LOG_CHANNEL_ID"),
        admin_role_id=as_int("ADMIN_ROLE_ID"),
        tz=ZoneInfo(_get(env, "TZ_NAME") or "America/New_York"),
        textfile_path=Path(textfile) if textfile else None,
    )
