# tower-bot

A Discord bot for Tower, the home Unraid server. It starts with a game night feature for a private Discord server, with room to grow into small ops notifications later. See the design spec in [GuessLee/unraid](https://github.com/GuessLee/unraid/blob/main/docs/superpowers/specs/2026-09-28-tower-bot-game-night-design.md) for the full plan.

## Dev

```bash
docker run -d --name tb-pg -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16
pip install -e ".[dev]"
pytest
```

Config lives only in `bot.env` plus secrets kept on Tower. Never commit tokens, connection strings, or other secrets to this repo.
