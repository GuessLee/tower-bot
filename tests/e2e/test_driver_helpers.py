"""Unit test for the pure helper the e2e Driver polls with. Not e2e itself
(no pytestmark here, no fixtures needing real Discord tokens), so it runs as
part of the default suite alongside every other unit test."""

from __future__ import annotations

from tests.e2e.conftest import embed_text


def test_embed_text_flattens_title_description_and_fields() -> None:
    message = {
        "embeds": [
            {
                "title": "🎮 Game Night - Wed Oct 1, 9:00 PM",
                "description": "React to vote.",
                "fields": [
                    {"name": "✅ In (1)", "value": "<@123>"},
                    {"name": "❔ Maybe (0)", "value": "-"},
                ],
            }
        ]
    }
    text = embed_text(message)
    assert "In (1)" in text
    assert "<@123>" in text
    assert "React to vote." in text


def test_embed_text_handles_missing_embed() -> None:
    assert embed_text({}) == " "


def test_embed_text_handles_no_fields() -> None:
    message = {"embeds": [{"title": "t", "description": "d"}]}
    assert embed_text(message) == "t d"
