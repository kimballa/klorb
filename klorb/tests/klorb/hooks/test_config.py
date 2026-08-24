# © Copyright 2026 Aaron Kimball
import pytest
from pydantic import ValidationError

from klorb.hooks.config import (
    EVENT_CONFIG_MODELS,
    EVENT_NAMES,
    HOOK_NAMES,
    PROCESS_SCOPED_HOOK_NAMES,
    EventConfig,
    FileSystemModifiedEventConfig,
    HookConfig,
    HookConfigFilter,
    TimerEventConfig,
    WorkspaceTrustChangedEventConfig,
    filter_heritable_events,
    filter_heritable_hooks,
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


def test_file_system_modified_event_config_apply_gitignore_defaults_false() -> None:
    config = FileSystemModifiedEventConfig.model_validate({
        "watch": ".", "action": {"type": "chat", "prompt": "changed"},
    })
    assert config.apply_gitignore is False


def test_file_system_modified_event_config_apply_gitignore_parses_via_alias() -> None:
    config = FileSystemModifiedEventConfig.model_validate({
        "watch": ".", "applyGitignore": True, "action": {"type": "chat", "prompt": "changed"},
    })
    assert config.apply_gitignore is True


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


def test_process_scoped_hook_names_covers_exactly_the_pre_session_and_lifetime_boundary_hooks() -> None:
    assert PROCESS_SCOPED_HOOK_NAMES == {
        "onProcessStart", "onProcessEnd", "onSessionStart", "onSessionEnd",
    }
    assert PROCESS_SCOPED_HOOK_NAMES.issubset(HOOK_NAMES)


def test_hook_config_is_heritable_defaults_true_and_round_trips_by_alias() -> None:
    hook = HookConfig.model_validate({"type": "chat", "prompt": "x"})
    assert hook.is_heritable is True
    explicit = HookConfig.model_validate({"type": "chat", "prompt": "x", "isHeritable": False})
    assert explicit.is_heritable is False
    assert explicit.model_dump(by_alias=True, exclude_defaults=True) == {
        "type": "chat", "prompt": "x", "isHeritable": False,
    }


def test_event_config_is_heritable_defaults_false_and_round_trips_by_alias() -> None:
    event = FileSystemModifiedEventConfig.model_validate({
        "watch": "src/", "action": {"type": "bash", "shell": "echo hi"},
    })
    assert event.is_heritable is False
    explicit = FileSystemModifiedEventConfig.model_validate({
        "watch": "src/", "action": {"type": "bash", "shell": "echo hi"}, "isHeritable": True,
    })
    assert explicit.is_heritable is True


def test_filter_heritable_hooks_drops_non_heritable_entries_and_empty_names() -> None:
    hooks = {
        "onToolUse": [
            HookConfig.model_validate({"type": "chat", "prompt": "a", "isHeritable": True}),
            HookConfig.model_validate({"type": "chat", "prompt": "b", "isHeritable": False}),
        ],
        "onToolResult": [
            HookConfig.model_validate({"type": "chat", "prompt": "c", "isHeritable": False}),
        ],
    }
    filtered = filter_heritable_hooks(hooks)
    assert list(filtered.keys()) == ["onToolUse"]
    assert [h.prompt for h in filtered["onToolUse"]] == ["a"]
    # Original dict is untouched.
    assert len(hooks["onToolUse"]) == 2


def test_filter_heritable_events_drops_non_heritable_entries_and_empty_names() -> None:
    events: dict[str, list[EventConfig]] = {
        "Timer": [
            TimerEventConfig.model_validate({
                "interval_minutes": 5, "action": {"type": "chat", "prompt": "tick"},
                "isHeritable": True,
            }),
        ],
        "WorkspaceTrustChanged": [
            WorkspaceTrustChangedEventConfig.model_validate({
                "action": {"type": "chat", "prompt": "trust"}, "isHeritable": False,
            }),
        ],
    }
    filtered = filter_heritable_events(events)
    assert list(filtered.keys()) == ["Timer"]
