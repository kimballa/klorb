# © Copyright 2026 Aaron Kimball
import logging

import pytest

from klorb.hooks.config import FileSystemModifiedEventConfig, HookConfig, TimerEventConfig
from klorb.hooks.merge import (
    concatenate_named_handler_lists,
    parse_event_dict,
    parse_handler_list,
    parse_session_scoped_hook_dict,
)


def test_concatenate_appends_a_new_layers_list_onto_an_existing_name() -> None:
    accumulator: dict[str, list[HookConfig]] = {
        "onProcessStart": [HookConfig(type="bash", shell="echo one")],
    }
    concatenate_named_handler_lists(
        accumulator, {"onProcessStart": [HookConfig(type="bash", shell="echo two")]})

    assert [handler.shell for handler in accumulator["onProcessStart"]] == ["echo one", "echo two"]


def test_concatenate_starts_a_new_name_with_an_empty_list() -> None:
    accumulator: dict[str, list[HookConfig]] = {}
    concatenate_named_handler_lists(
        accumulator, {"onSessionStart": [HookConfig(type="bash", shell="echo hi")]})

    assert list(accumulator.keys()) == ["onSessionStart"]
    assert len(accumulator["onSessionStart"]) == 1


def test_parse_handler_list_validates_each_entry() -> None:
    parsed, warnings = parse_handler_list(
        [{"type": "bash", "shell": "echo hi"}], model=HookConfig, source_label="test-layer")

    assert warnings == []
    assert len(parsed) == 1
    assert parsed[0].type == "bash"
    assert parsed[0].shell == "echo hi"


def test_parse_handler_list_skips_an_invalid_entry_and_keeps_the_rest() -> None:
    parsed, warnings = parse_handler_list(
        [{"type": "bash", "shell": "echo hi"}, {"type": "not-a-real-type"}],
        model=HookConfig, source_label="test-layer")

    assert len(parsed) == 1
    assert len(warnings) == 1
    assert "test-layer" in warnings[0]


def test_parse_handler_list_warns_when_value_is_not_a_list() -> None:
    parsed, warnings = parse_handler_list(
        {"not": "a list"}, model=HookConfig, source_label="test-layer")

    assert parsed == []
    assert len(warnings) == 1
    assert "test-layer" in warnings[0]


def test_parse_session_scoped_hook_dict_forces_is_heritable_default() -> None:
    result = parse_session_scoped_hook_dict(
        {"onToolUse": [{"type": "bash", "shell": "echo hi"}]}, source_label="test-source")

    assert result["onToolUse"][0].is_heritable is False


def test_parse_session_scoped_hook_dict_honors_an_explicit_is_heritable() -> None:
    result = parse_session_scoped_hook_dict(
        {"onToolUse": [{"type": "bash", "shell": "echo hi", "isHeritable": True}]},
        source_label="test-source")

    assert result["onToolUse"][0].is_heritable is True


def test_parse_session_scoped_hook_dict_drops_process_scoped_names(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        result = parse_session_scoped_hook_dict(
            {"onSessionEnd": [{"type": "bash", "shell": "echo hi"}]}, source_label="test-source")

    assert result == {}
    assert "process-scoped hook" in caplog.text


def test_parse_session_scoped_hook_dict_drops_unrecognized_names(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        result = parse_session_scoped_hook_dict(
            {"onTotallyMadeUp": [{"type": "bash", "shell": "echo hi"}]}, source_label="test-source")

    assert result == {}
    assert "onTotallyMadeUp" in caplog.text


def test_parse_session_scoped_hook_dict_malformed_shape_yields_empty_dict(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        assert parse_session_scoped_hook_dict("not a dict", source_label="test-source") == {}


def test_parse_event_dict_resolves_the_right_subclass_per_name() -> None:
    result = parse_event_dict(
        {"FileSystemModified": [{"watch": "src/", "action": {"type": "chat", "prompt": "x"}}]},
        source_label="test-source")

    fs_event = result["FileSystemModified"][0]
    assert isinstance(fs_event, FileSystemModifiedEventConfig)
    assert fs_event.watch == "src/"


def test_parse_event_dict_clamps_timer_intervals_below_the_floor() -> None:
    result = parse_event_dict(
        {"Timer": [{"interval_minutes": 0.01, "action": {"type": "chat", "prompt": "tick"}}]},
        source_label="test-source")

    timer_event = result["Timer"][0]
    assert isinstance(timer_event, TimerEventConfig)
    assert timer_event.interval_minutes == pytest.approx(10.0 / 60.0)


def test_parse_event_dict_drops_unrecognized_names(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        result = parse_event_dict(
            {"TotallyMadeUp": [{"action": {"type": "bash", "shell": "x"}}]}, source_label="test-source")

    assert result == {}
    assert "TotallyMadeUp" in caplog.text
