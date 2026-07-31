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
        assert message.body() == json.dumps([
            {"type": "text", "text": "attachment"},
            {"type": "text", "text": "prompt"},
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
