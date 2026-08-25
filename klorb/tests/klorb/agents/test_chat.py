# © Copyright 2026 Aaron Kimball
"""Tests for klorb.agents.chat.Channel/ChatMessage/chat_nickname."""
import threading
from collections.abc import Callable
from unittest.mock import MagicMock

from klorb.agents.chat import CHAT_UNREAD_INTERJECTION_SUBJECT, Channel, chat_nickname
from klorb.agents.runtime import SubagentHandle, SubagentTurnOutcome
from klorb.session import Session, SessionConfig


def _make_session(
    make_session_config: Callable[..., SessionConfig], **overrides: object,
) -> Session:
    return Session(make_session_config(**overrides), provider=MagicMock())


def _register_child(parent: Session, *, role: str = "explorer", title: str = "task") -> Session:
    child_config = parent.config.model_copy(deep=True)
    child_config.role_name = role
    child = Session(child_config, provider=MagicMock(), parent=parent)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None),
        cancel_event=threading.Event(), role=role, title=title,
        outcome=SubagentTurnOutcome(output="done", completed=True))
    parent.subagent_tracker.register(handle)
    return child


def test_post_assigns_increasing_sequence_numbers(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    channel = Channel()

    first = channel.post(session.id, "hello", session)
    second = channel.post(session.id, "world", session)

    assert first.seq == 1
    assert second.seq == 2


def test_post_resolves_mentions_by_raw_session_id(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    child = _register_child(session)
    channel = Channel()

    message = channel.post(session.id, f"hi @{child.id}", session)

    assert message.mentions == [child.id]
    assert message.unresolved_mentions == []


def test_post_resolves_mentions_by_role_address_nickname(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    child = _register_child(session)
    channel = Channel()

    nickname = chat_nickname(child)
    message = channel.post(session.id, f"hi @{nickname}", session)

    assert message.mentions == [child.id]


def test_post_resolves_the_user_mention(make_session_config: Callable[..., SessionConfig]) -> None:
    session = _make_session(make_session_config)
    channel = Channel()

    message = channel.post(session.id, "cc @user please review", session)

    assert message.mentions == ["user"]


def test_post_records_unresolved_mentions(make_session_config: Callable[..., SessionConfig]) -> None:
    session = _make_session(make_session_config)
    channel = Channel()

    message = channel.post(session.id, "hi @nobody-here", session)

    assert message.mentions == []
    assert message.unresolved_mentions == ["nobody-here"]


def test_post_deduplicates_repeated_mentions(make_session_config: Callable[..., SessionConfig]) -> None:
    session = _make_session(make_session_config)
    child = _register_child(session)
    channel = Channel()

    message = channel.post(session.id, f"@{child.id} ping @{child.id} again", session)

    assert message.mentions == [child.id]


def test_post_advances_the_posters_own_hwm(make_session_config: Callable[..., SessionConfig]) -> None:
    session = _make_session(make_session_config)
    channel = Channel()

    channel.post(session.id, "one", session)
    message = channel.post(session.id, "two", session)

    assert channel.unread_count(session.id) == 0
    assert channel.read_and_advance(session.id) == []
    assert message.seq == 2


def test_register_participant_seeds_hwm_at_current_seq(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    other = _make_session(make_session_config)
    channel = Channel()
    channel.post(session.id, "before you joined", session)

    channel.register_participant(other.id)

    assert channel.unread_count(other.id) == 0
    channel.post(session.id, "after you joined", session)
    assert channel.unread_count(other.id) == 1


def test_register_participant_does_not_overwrite_an_existing_hwm(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    channel = Channel()
    channel.post(session.id, "one", session)

    channel.register_participant(session.id, at_seq=0)

    assert channel.unread_count(session.id) == 0


def test_read_and_advance_returns_unread_oldest_first_and_moves_the_hwm(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    reader = _make_session(make_session_config)
    channel = Channel()
    channel.register_participant(reader.id, at_seq=0)
    channel.post(session.id, "one", session)
    channel.post(session.id, "two", session)

    first_batch = channel.read_and_advance(reader.id)
    assert [m.body for m in first_batch] == ["one", "two"]
    assert channel.read_and_advance(reader.id) == []


def test_read_and_advance_respects_limit(make_session_config: Callable[..., SessionConfig]) -> None:
    session = _make_session(make_session_config)
    reader = _make_session(make_session_config)
    channel = Channel()
    channel.register_participant(reader.id, at_seq=0)
    channel.post(session.id, "one", session)
    channel.post(session.id, "two", session)

    first = channel.read_and_advance(reader.id, limit=1)
    assert [m.body for m in first] == ["one"]
    assert channel.unread_count(reader.id) == 1
    second = channel.read_and_advance(reader.id, limit=1)
    assert [m.body for m in second] == ["two"]


def test_unread_mention_count_only_counts_unread_messages_mentioning_the_participant(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    reader = _register_child(session)
    channel = Channel()
    channel.register_participant(reader.id, at_seq=0)
    channel.post(session.id, f"@{reader.id} hi", session)
    channel.post(session.id, "unrelated", session)

    assert channel.unread_mention_count(reader.id) == 1
    channel.read_and_advance(reader.id)
    assert channel.unread_mention_count(reader.id) == 0


def test_history_returns_everything_regardless_of_hwm(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    channel = Channel()
    channel.post(session.id, "one", session)
    channel.post(session.id, "two", session)
    channel.read_and_advance(session.id)

    assert [m.body for m in channel.history()] == ["one", "two"]
    assert [m.body for m in channel.history(limit=1)] == ["two"]


def test_max_history_trims_the_oldest_retained_messages_without_reusing_seq(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    channel = Channel(max_history=2)

    channel.post(session.id, "one", session)
    channel.post(session.id, "two", session)
    channel.post(session.id, "three", session)

    assert [m.body for m in channel.history()] == ["two", "three"]
    assert [m.seq for m in channel.history()] == [2, 3]


def test_snapshot_and_restore_round_trip(make_session_config: Callable[..., SessionConfig]) -> None:
    session = _make_session(make_session_config)
    channel = Channel()
    channel.post(session.id, "one", session)
    channel.increment_mention_wake_count()

    messages, hwm, next_seq, mention_wake_count = channel.snapshot()
    restored = Channel.restore(messages, hwm, next_seq, mention_wake_count)

    assert [m.body for m in restored.history()] == ["one"]
    assert restored.mention_wake_count() == 1
    restored_message = restored.post(session.id, "two", session)
    assert restored_message.seq == 2


def test_dirty_flag_tracks_mutation(make_session_config: Callable[..., SessionConfig]) -> None:
    session = _make_session(make_session_config)
    channel = Channel()
    assert not channel.is_dirty()

    channel.post(session.id, "one", session)
    assert channel.is_dirty()

    channel.mark_persisted()
    assert not channel.is_dirty()


def test_chat_nickname_for_the_literal_user() -> None:
    assert chat_nickname("user") == "user"


def test_chat_nickname_for_a_live_session(make_session_config: Callable[..., SessionConfig]) -> None:
    session = _make_session(make_session_config, role_name="explorer")
    assert chat_nickname(session) == f"explorer-{session.address()}"


def test_chat_nickname_for_a_stale_id_falls_back_to_the_raw_id() -> None:
    assert chat_nickname("1706040000-blue-otter") == "1706040000-blue-otter"


def test_chat_unread_interjection_registered_on_construction(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    assert CHAT_UNREAD_INTERJECTION_SUBJECT in session._standing_interjection_providers


def test_chat_unread_interjection_is_none_when_caught_up(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    provider = session._standing_interjection_providers[CHAT_UNREAD_INTERJECTION_SUBJECT]
    assert provider() is None


def test_chat_unread_interjection_reports_unread_count(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    other = _register_child(session)
    session.chat_channel.post(other.id, "hello", other)

    provider = session._standing_interjection_providers[CHAT_UNREAD_INTERJECTION_SUBJECT]
    text = provider()
    assert text is not None
    assert "1 unread chat room message" in text
    assert "@mention" not in text


def test_chat_unread_interjection_calls_out_mentions_separately(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    other = _register_child(session)
    session.chat_channel.post(other.id, f"@{session.id} hi", other)

    provider = session._standing_interjection_providers[CHAT_UNREAD_INTERJECTION_SUBJECT]
    text = provider()
    assert text is not None
    assert "1 unread chat room message" in text
    assert "1 that @mention you directly" in text


def test_chat_unread_interjection_self_quiets_after_reading(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _make_session(make_session_config)
    other = _register_child(session)
    session.chat_channel.post(other.id, "hello", other)
    session.chat_channel.read_and_advance(session.id)

    provider = session._standing_interjection_providers[CHAT_UNREAD_INTERJECTION_SUBJECT]
    assert provider() is None
