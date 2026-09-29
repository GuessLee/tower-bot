from pathlib import Path

import pytest

from tower_bot.config import ConfigError, load_config

BASE = {
    "DISCORD_TOKEN": "tok",
    "DATABASE_URL": "postgresql://gamebot@gulag-postgres:5432/gamebot",
    "GITHUB_TOKEN": "ghp",
    "GUILD_ID": "1",
    "NIGHT_CHANNEL_ID": "2",
    "LIBRARY_CHANNEL_ID": "3",
    "LOG_CHANNEL_ID": "4",
    "ADMIN_ROLE_ID": "5",
}


def test_defaults() -> None:
    c = load_config(BASE)
    assert c.github_repo == "GuessLee/unraid"
    assert c.feedback_labels == ("feedback", "classify-me")
    assert str(c.tz) == "America/New_York"
    assert c.db_password is None and c.textfile_path is None
    assert (c.guild_id, c.night_channel_id, c.admin_role_id) == (1, 2, 5)


def test_file_variant_wins_and_is_stripped(tmp_path: Path) -> None:
    f = tmp_path / "t"
    f.write_text("from-file\n")
    env = {**BASE, "DISCORD_TOKEN_FILE": str(f), "DB_PASSWORD_FILE": str(f)}
    c = load_config(env)
    assert c.discord_token == "from-file"
    assert c.db_password == "from-file"


def test_missing_lists_everything() -> None:
    with pytest.raises(ConfigError) as e:
        load_config({"DISCORD_TOKEN": "x"})
    msg = str(e.value)
    for key in ("DATABASE_URL", "GITHUB_TOKEN", "GUILD_ID", "ADMIN_ROLE_ID"):
        assert key in msg


def test_bad_int() -> None:
    with pytest.raises(ConfigError, match="GUILD_ID"):
        load_config({**BASE, "GUILD_ID": "abc"})


def test_labels_and_textfile() -> None:
    c = load_config(
        {
            **BASE,
            "FEEDBACK_LABELS": "feedback, e2e-test",
            "TEXTFILE_PATH": "/textfile/tower_bot.prom",
        }
    )
    assert c.feedback_labels == ("feedback", "e2e-test")
    assert c.textfile_path == Path("/textfile/tower_bot.prom")


def test_unreadable_secret_file(tmp_path: Path) -> None:
    nonexistent = tmp_path / "nonexistent"
    env = {**BASE, "DISCORD_TOKEN_FILE": str(nonexistent)}
    with pytest.raises(ConfigError, match="DISCORD_TOKEN_FILE"):
        load_config(env)
