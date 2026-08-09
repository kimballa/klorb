# © Copyright 2026 Aaron Kimball
import pytest
from pydantic import ValidationError

from klorb.hooks.config import (
    EVENT_CONFIG_MODELS,
    EVENT_NAMES,
    HOOK_NAMES,
    FileSystemModifiedEventConfig,
    HookConfig,
    HookConfigFilter,
    TimerEventConfig,
    WorkspaceTrustChangedEventConfig,
)


def test_hook_config_requires_a_type() -> None:
    with pytest.raises(ValidationError):
        HookConfig.model_validate({})


def test_hook_config_bash_shape() -> None:
    hook = HookConfig.model_validate({"type": "bash", "command": ["echo", "hi"], "name": "greet"})
    assert hook.type == "bash"
    assert hook.command == ["echo", "hi"]
    assert hook.name == "greet"
    assert hook.filter is None


def test_hook_config_filter_accepts_the_reserved_word_not_via_alias() -> None:
    filter_ = HookConfigFilter.model_validate({"not": {"matches": "done"}})
    assert filter_.not_ is not None
    assert filter_.not_.matches == "done"


def test_hook_config_filter_round_trips_by_alias() -> None:
    filter_ = HookConfigFilter.model_validate({"not": {"matches": "done"}})
    assert filter_.model_dump(by_alias=True) == {
        "matches": None, "pattern": None, "contains": None, "any": None, "all": None,
        "not": {
            "matches": "done", "pattern": None, "contains": None, "any": None, "all": None,
            "not": None,
        },
    }


def test_event_config_models_cover_every_event_name() -> None:
    assert set(EVENT_CONFIG_MODELS.keys()) == EVENT_NAMES


def test_file_system_modified_event_config_requires_watch_and_action() -> None:
    config = FileSystemModifiedEventConfig.model_validate({
        "watch": "src/", "action": {"type": "bash", "shell": "echo changed"},
    })
    assert config.watch == "src/"
    assert config.action.shell == "echo changed"

    with pytest.raises(ValidationError):
        FileSystemModifiedEventConfig.model_validate({"action": {"type": "bash", "shell": "x"}})


def test_timer_event_config_accepts_interval_or_cron() -> None:
    interval = TimerEventConfig.model_validate({
        "interval_minutes": 10, "action": {"type": "chat", "prompt": "tick"},
    })
    assert interval.interval_minutes == 10
    assert interval.cron is None

    cron = TimerEventConfig.model_validate({
        "cron": "30 2 * * *", "action": {"type": "chat", "prompt": "tick"},
    })
    assert cron.cron == "30 2 * * *"


def test_workspace_trust_changed_event_config_has_no_selector_field() -> None:
    config = WorkspaceTrustChangedEventConfig.model_validate({
        "action": {"type": "chat", "prompt": "trust changed"},
    })
    assert config.action.prompt == "trust changed"


def test_hook_names_matches_the_documented_lifecycle_moments() -> None:
    assert HOOK_NAMES == {
        "onProcessStart", "onSessionStart", "onSubmitUserPrompt", "onRequestPermission",
        "onToolUse", "onToolResult", "onActivateSkill", "onSubagentStart", "onSubagentTurnEnd",
        "onAgentTurnEnd", "onSessionEnd", "onProcessEnd",
    }


def test_hook_config_filter_rejects_invalid_regex_pattern() -> None:
    with pytest.raises(ValidationError, match="invalid regex pattern"):
        HookConfigFilter.model_validate({"pattern": "["})


def test_hook_config_filter_accepts_valid_regex_pattern() -> None:
    filter_ = HookConfigFilter.model_validate({"pattern": r"^foo\d+"})
    assert filter_.pattern == r"^foo\d+"
