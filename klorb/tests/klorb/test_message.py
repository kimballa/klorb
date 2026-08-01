# © Copyright 2026 Aaron Kimball
"""Tests for klorb.message -- Message.body()/provider_content() and MessageFragment."""

import json
from datetime import datetime
from typing import Any

from klorb.message import Message, MessageFragment


def _message(**overrides: object) -> Message:
    defaults: dict[str, Any] = dict(
        content="hello", role="user", num_tokens=0, processing_state="complete",
        timestamp=datetime.now(),
    )
    defaults.update(overrides)
    return Message(**defaults)


class TestBody:
    """Tests for Message.body()."""

    def test_returns_content_by_default(self) -> None:
        assert _message(content="hello").body() == "hello"

    def test_defaults_to_empty_string_content(self) -> None:
        assert _message(content="").body() == ""

    def test_prefers_streaming_content_over_content(self) -> None:
        message = _message(content="", streaming_content=["hel", "lo"])
        assert message.body() == "hello"

    def test_prefers_fragments_over_streaming_content_and_content(self) -> None:
        message = _message(
            content="ignored",
            streaming_content=["ignored too"],
            fragments=[
                MessageFragment(type="text", text="attachment"),
                MessageFragment(type="text", text="prompt"),
            ],
        )
        fragment_json = {
            "image_url": None, "image_path": None, "mime_type": None,
            "source_filename": None, "original_width": None, "original_height": None,
        }
        assert message.body() == json.dumps([
            {"type": "text", "text": "attachment", **fragment_json},
            {"type": "text", "text": "prompt", **fragment_json},
        ])


class TestProviderContent:
    """Tests for Message.provider_content()."""

    def test_returns_content_string_when_no_fragments(self) -> None:
        assert _message(content="hello").provider_content() == "hello"

    def test_returns_fragment_dicts_when_fragments_set(self) -> None:
        message = _message(
            content="ignored",
            fragments=[MessageFragment(type="text", text="a"), MessageFragment(type="text", text="b")],
        )
        assert message.provider_content() == [
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
        ]

    def test_image_fragment_dumps_to_exact_wire_shape_excluding_bookkeeping(self) -> None:
        message = _message(fragments=[MessageFragment(
            type="image_url", image_url={"url": "data:image/webp;base64,xx"},
            mime_type="image/webp", source_filename="foo.png",
            original_width=800, original_height=600, resized_width=400, resized_height=300,
            estimated_tokens=123,
        )])
        assert message.provider_content() == [
            {"type": "image_url", "image_url": {"url": "data:image/webp;base64,xx"}},
        ]


class TestForPersistence:
    """Tests for Message.for_persistence()."""

    def test_returns_self_when_no_fragments(self) -> None:
        message = _message(content="hello")
        assert message.for_persistence() is message

    def test_returns_self_when_no_spilled_image_fragments(self) -> None:
        message = _message(fragments=[MessageFragment(type="text", text="a")])
        assert message.for_persistence() is message

    def test_clears_image_url_once_image_path_is_set(self) -> None:
        message = _message(fragments=[MessageFragment(
            type="image_url", image_url={"url": "data:image/webp;base64,xx"},
            image_path="images/a.webp", mime_type="image/webp")])

        persisted = message.for_persistence()

        assert persisted is not message
        assert persisted.fragments is not None
        assert persisted.fragments[0].image_url is None
        assert persisted.fragments[0].image_path == "images/a.webp"
        # The original in-memory message is untouched -- still safe to send this turn.
        assert message.fragments is not None
        assert message.fragments[0].image_url == {"url": "data:image/webp;base64,xx"}
