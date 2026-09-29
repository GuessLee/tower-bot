from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest

from tower_bot.core.discord_gateway import DiscordGateway, map_http_error, to_discord_embed
from tower_bot.core.embed import EmbedSpec, Field
from tower_bot.core.gateway import MessageGone, TransientGatewayError


def _http(status: int, cls: type[discord.HTTPException]) -> discord.HTTPException:
    resp = MagicMock()
    resp.status = status
    resp.reason = "x"
    return cls(resp, "msg")


def test_map_errors() -> None:
    assert isinstance(map_http_error(_http(404, discord.NotFound)), MessageGone)
    assert isinstance(map_http_error(_http(503, discord.DiscordServerError)), TransientGatewayError)
    forbidden = _http(403, discord.Forbidden)
    assert map_http_error(forbidden) is forbidden


def test_to_discord_embed() -> None:
    e = to_discord_embed(
        EmbedSpec(
            title="T",
            description="D",
            fields=(Field("a", "b", True),),
            footer="F",
            color=1,
            url="https://x",
        )
    )
    assert (e.title, e.description, e.footer.text, e.url) == ("T", "D", "F", "https://x")
    assert e.fields[0].name == "a" and e.fields[0].inline


class _Reaction:
    def __init__(self, emoji: str, ids: list[int], fail_after: int | None = None) -> None:
        self.emoji, self._ids, self._fail_after = emoji, ids, fail_after

    async def _iter(self) -> AsyncIterator[SimpleNamespace]:
        for i, uid in enumerate(self._ids):
            if self._fail_after is not None and i == self._fail_after:
                raise _http(503, discord.DiscordServerError)
            yield SimpleNamespace(id=uid)

    def users(self, *, limit: int | None = None) -> AsyncIterator[SimpleNamespace]:
        return self._iter()


def _gateway(reactions: list[_Reaction], fetch_error: Exception | None = None) -> DiscordGateway:
    async def fetch() -> SimpleNamespace:
        if fetch_error is not None:
            raise fetch_error
        return SimpleNamespace(reactions=reactions)

    pm = SimpleNamespace(fetch=fetch)
    client = MagicMock()
    client.user = SimpleNamespace(id=999)
    client.get_partial_messageable.return_value.get_partial_message.return_value = pm
    return DiscordGateway(client)


async def test_reactions_snapshot_drops_bot_user() -> None:
    gw = _gateway([_Reaction("✅", [1, 999, 2]), _Reaction("1️⃣", [999])])
    assert await gw.reactions(10, 20) == {"✅": frozenset({1, 2}), "1️⃣": frozenset()}


async def test_reactions_error_mid_pagination_is_transient() -> None:
    gw = _gateway([_Reaction("✅", [1, 2, 3], fail_after=1)])
    with pytest.raises(TransientGatewayError):
        await gw.reactions(10, 20)


async def test_reactions_deleted_message_is_gone_and_forbidden_passes_through() -> None:
    with pytest.raises(MessageGone):
        await _gateway([], fetch_error=_http(404, discord.NotFound)).reactions(10, 20)
    forbidden = _http(403, discord.Forbidden)
    with pytest.raises(discord.Forbidden) as info:
        await _gateway([], fetch_error=forbidden).reactions(10, 20)
    assert info.value is forbidden and info.value.__cause__ is None
