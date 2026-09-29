import pytest

from tests.fakes import FakeGateway
from tower_bot.core.embed import EmbedSpec
from tower_bot.core.gateway import TransientGatewayError


@pytest.mark.parametrize(
    "op",
    [
        "post",
        "edit",
        "add_reactions",
        "reactions",
        "reply",
        "pin",
        "create_event",
        "edit_event",
        "delete_event",
    ],
)
async def test_fail_next_all_ops(op: str) -> None:
    """Test that fail_next works for all Gateway operations."""
    gateway = FakeGateway()

    # Set up state needed for each operation
    embed = EmbedSpec(title="test")

    if op == "post":
        gateway.fail_next("post")
        with pytest.raises(TransientGatewayError):
            await gateway.post(123, embed)
        # Should succeed on second call
        msg_id = await gateway.post(123, embed)
        assert msg_id > 0

    elif op == "edit":
        msg_id = await gateway.post(123, embed)
        gateway.fail_next("edit")
        with pytest.raises(TransientGatewayError):
            await gateway.edit(123, msg_id, embed)
        # Should succeed on second call
        await gateway.edit(123, msg_id, embed)

    elif op == "add_reactions":
        msg_id = await gateway.post(123, embed)
        gateway.fail_next("add_reactions")
        with pytest.raises(TransientGatewayError):
            await gateway.add_reactions(123, msg_id, ["👍"])
        # Should succeed on second call
        await gateway.add_reactions(123, msg_id, ["👍"])

    elif op == "reactions":
        msg_id = await gateway.post(123, embed)
        await gateway.add_reactions(123, msg_id, ["👍"])
        gateway.fail_next("reactions")
        with pytest.raises(TransientGatewayError):
            await gateway.reactions(123, msg_id)
        # Should succeed on second call
        result = await gateway.reactions(123, msg_id)
        assert "👍" in result

    elif op == "reply":
        msg_id = await gateway.post(123, embed)
        gateway.fail_next("reply")
        with pytest.raises(TransientGatewayError):
            await gateway.reply(123, msg_id, "test reply")
        # Should succeed on second call
        reply_id = await gateway.reply(123, msg_id, "test reply")
        assert reply_id > 0

    elif op == "pin":
        msg_id = await gateway.post(123, embed)
        gateway.fail_next("pin")
        with pytest.raises(TransientGatewayError):
            await gateway.pin(123, msg_id)
        # Should succeed on second call
        await gateway.pin(123, msg_id)
        assert msg_id in gateway.pinned

    elif op == "create_event":
        from datetime import datetime

        gateway.fail_next("create_event")
        with pytest.raises(TransientGatewayError):
            await gateway.create_event(
                456,
                "test event",
                datetime(2026, 10, 1, 18, 0),
                datetime(2026, 10, 1, 20, 0),
                "test",
            )
        # Should succeed on second call
        event_id = await gateway.create_event(
            456,
            "test event",
            datetime(2026, 10, 1, 18, 0),
            datetime(2026, 10, 1, 20, 0),
            "test",
        )
        assert event_id > 0

    elif op == "edit_event":
        from datetime import datetime

        event_id = await gateway.create_event(
            456,
            "test event",
            datetime(2026, 10, 1, 18, 0),
            datetime(2026, 10, 1, 20, 0),
            "test",
        )
        gateway.fail_next("edit_event")
        with pytest.raises(TransientGatewayError):
            await gateway.edit_event(
                456,
                event_id,
                datetime(2026, 10, 1, 19, 0),
                datetime(2026, 10, 1, 21, 0),
                "updated",
            )
        # Should succeed on second call
        await gateway.edit_event(
            456,
            event_id,
            datetime(2026, 10, 1, 19, 0),
            datetime(2026, 10, 1, 21, 0),
            "updated",
        )

    elif op == "delete_event":
        from datetime import datetime

        event_id = await gateway.create_event(
            456,
            "test event",
            datetime(2026, 10, 1, 18, 0),
            datetime(2026, 10, 1, 20, 0),
            "test",
        )
        gateway.fail_next("delete_event")
        with pytest.raises(TransientGatewayError):
            await gateway.delete_event(456, event_id)
        # Should succeed on second call
        await gateway.delete_event(456, event_id)
        assert event_id not in gateway.events
