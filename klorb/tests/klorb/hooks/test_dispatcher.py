# © Copyright 2026 Aaron Kimball
"""Tests for klorb.hooks.dispatcher.HookDispatcher: chain resolution, filtering, chaining a
handler's output into the next handler's input, and folding results into one aggregate
`HookOutput`."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from klorb.api_provider import ProviderResponse
from klorb.hooks.config import (
    PROCESS_SCOPED_HOOK_NAMES,
    HookConfig,
    HookConfigFilter,
    WorkspaceTrustChangedEventConfig,
)
from klorb.hooks.dispatcher import HookDispatcher
from klorb.hooks.hook_api import EventInput, HookInput
from klorb.message import Message
from klorb.permissions.directory_access import DirRules
from klorb.process_config import ProcessConfig
from klorb.session.config import SessionConfig
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def _unsandboxed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("klorb.hooks.bash_handler.bwrap_available", lambda: False)


def _process_config(workspace_root: Path, hooks: dict[str, list[HookConfig]]) -> ProcessConfig:
    """Route `hooks` the same way `load_process_config()`'s push-down split does: a
    `PROCESS_SCOPED_HOOK_NAMES` entry stays on `ProcessConfig.hooks`, every other name lands on
    `session.hooks`."""
    process_hooks = {name: h for name, h in hooks.items() if name in PROCESS_SCOPED_HOOK_NAMES}
    session_hooks = {name: h for name, h in hooks.items() if name not in PROCESS_SCOPED_HOOK_NAMES}
    session = SessionConfig(
        workspace=Workspace(path=workspace_root, trusted=True),
        read_dirs=DirRules(allow=[workspace_root]),
        write_dirs=DirRules(allow=[workspace_root]),
        hooks=session_hooks)
    return ProcessConfig(session=session, hooks=process_hooks)


def _hook_input(workspace_root: Path, **overrides: Any) -> HookInput:
    defaults: dict[str, Any] = {
        "hook": "onProcessStart", "reason": "Startup", "workspace_root": str(workspace_root),
    }
    defaults.update(overrides)
    return HookInput(**defaults)


def test_dispatch_with_no_configured_handlers_returns_default_success(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {})
    result = HookDispatcher(process_config).dispatch("onProcessStart", _hook_input(tmp_path))
    assert result.success is True
    assert result.message is None


def test_dispatch_runs_a_single_bash_handler(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onProcessStart": [HookConfig(type="bash", shell='echo \'{"message": "hi"}\'')],
    })
    result = HookDispatcher(process_config).dispatch("onProcessStart", _hook_input(tmp_path))
    assert result.message == "hi"


def test_dispatch_chains_message_into_the_next_handler(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onProcessStart": [
            HookConfig(type="bash", shell='echo \'{"message": "first"}\''),
            HookConfig(
                type="bash",
                shell=(
                    'python3 -c \'import sys, json; data = json.load(sys.stdin); '
                    'print(json.dumps({"message": data["message"] + "+second"}))\'')),
        ],
    })
    result = HookDispatcher(process_config).dispatch("onProcessStart", _hook_input(tmp_path))
    assert result.message == "first+second"


def test_dispatch_skips_a_handler_whose_filter_does_not_match(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onProcessStart": [
            HookConfig(
                type="bash", shell='echo \'{"message": "should not run"}\'',
                filter=HookConfigFilter(matches="SomethingElse")),
        ],
    })
    result = HookDispatcher(process_config).dispatch(
        "onProcessStart", _hook_input(tmp_path, reason="Startup"))
    assert result.message is None


def test_dispatch_runs_a_handler_whose_filter_matches(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onProcessStart": [
            HookConfig(
                type="bash", shell='echo \'{"message": "ran"}\'',
                filter=HookConfigFilter(matches="Startup")),
        ],
    })
    result = HookDispatcher(process_config).dispatch(
        "onProcessStart", _hook_input(tmp_path, reason="Startup"))
    assert result.message == "ran"


def test_dispatch_skips_a_classifier_handler_with_no_api_provider_wired_in(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onAgentTurnEnd": [HookConfig(type="classifier", prompt="classify this")],
    })
    result = HookDispatcher(process_config).dispatch(
        "onAgentTurnEnd", _hook_input(tmp_path, hook="onAgentTurnEnd"),
        session_config=process_config.session)
    assert result.success is True
    assert result.message is None


def test_dispatch_runs_a_chat_handler_as_its_own_configured_prompt(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onAgentTurnEnd": [HookConfig(type="chat", prompt="keep going")],
    })
    result = HookDispatcher(process_config).dispatch(
        "onAgentTurnEnd", _hook_input(tmp_path, hook="onAgentTurnEnd"),
        session_config=process_config.session)
    assert result.message == "keep going"


def test_dispatch_logs_a_handlers_log_at_info(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    process_config = _process_config(tmp_path, {
        "onProcessStart": [
            HookConfig(name="my-handler", type="bash", shell='echo \'{"log": "debug note"}\''),
        ],
    })
    with caplog.at_level("INFO"):
        result = HookDispatcher(process_config).dispatch("onProcessStart", _hook_input(tmp_path))
    assert result.log == "debug note"
    assert "Hook 'onProcessStart' handler 'my-handler': debug note" in caplog.text


def test_dispatch_folds_log_as_latest_handler_wins(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onProcessStart": [
            HookConfig(type="bash", shell='echo \'{"log": "first"}\''),
            HookConfig(type="bash", shell='echo \'{"message": "no opinion on log"}\''),
        ],
    })
    result = HookDispatcher(process_config).dispatch("onProcessStart", _hook_input(tmp_path))
    assert result.log == "first"


def test_dispatch_folds_success_as_strictest_outcome(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onRequestPermission": [
            HookConfig(type="bash", shell='echo \'{"success": true}\''),
            HookConfig(type="bash", shell='echo \'{"success": false}\''),
            HookConfig(type="bash", shell='echo \'{"success": true}\''),
        ],
    })
    result = HookDispatcher(process_config).dispatch(
        "onRequestPermission", _hook_input(tmp_path, hook="onRequestPermission"),
        session_config=process_config.session)
    assert result.success is False


def test_dispatch_folds_reset_session_once_any_handler_sets_it(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onAgentTurnEnd": [
            HookConfig(type="bash", shell='echo \'{"message": "first"}\''),
            HookConfig(type="bash", shell='echo \'{"reset_session": true}\''),
        ],
    })
    result = HookDispatcher(process_config).dispatch(
        "onAgentTurnEnd", _hook_input(tmp_path, hook="onAgentTurnEnd"),
        session_config=process_config.session)
    assert result.reset_session is True
    assert result.message == "first"


def test_dispatch_drops_reset_session_without_a_message(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    process_config = _process_config(tmp_path, {
        "onAgentTurnEnd": [HookConfig(type="bash", shell='echo \'{"reset_session": true}\'')],
    })
    with caplog.at_level("WARNING"):
        result = HookDispatcher(process_config).dispatch(
            "onAgentTurnEnd", _hook_input(tmp_path, hook="onAgentTurnEnd"),
            session_config=process_config.session)
    assert result.reset_session is False
    assert "reset_session" in caplog.text


@pytest.mark.parametrize("hook_name", ["onSessionEnd", "onSubmitUserPrompt", "onToolUse"])
def test_dispatch_drops_reset_session_for_hooks_outside_the_allowlist(
    tmp_path: Path, hook_name: str,
) -> None:
    """`RESET_SESSION_CAPABLE_HOOKS` is the only hook/event set `reset_session` survives
    folding for."""
    process_config = _process_config(tmp_path, {
        hook_name: [
            HookConfig(type="bash", shell='echo \'{"message": "restart me", "reset_session": true}\''),
        ],
    })
    result = HookDispatcher(process_config).dispatch(
        hook_name, _hook_input(tmp_path, hook=hook_name, reason="x"),
        session_config=process_config.session)
    assert result.reset_session is False
    assert result.message == "restart me"


def test_dispatch_a_failing_handler_contributes_nothing_to_the_chain(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onProcessStart": [
            HookConfig(type="bash", shell="exit 1"),
            HookConfig(type="bash", shell='echo \'{"message": "second ran fine"}\''),
        ],
    })
    result = HookDispatcher(process_config).dispatch("onProcessStart", _hook_input(tmp_path))
    assert result.success is True
    assert result.message == "second ran fine"


def _classifier_reply(message: str) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=json.dumps({"message": message}), role="assistant", num_tokens=1,
            timestamp=datetime.now(), processing_state="complete"),
        prompt_tokens=1)


def test_dispatch_runs_a_classifier_handler_when_an_api_provider_is_wired_in(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _classifier_reply("classified message")
    process_config = _process_config(tmp_path, {
        "onAgentTurnEnd": [HookConfig(type="classifier", prompt="summarize")],
    })
    result = HookDispatcher(process_config, api_provider=provider).dispatch(
        "onAgentTurnEnd", _hook_input(tmp_path, hook="onAgentTurnEnd"),
        session_config=process_config.session)
    assert result.message == "classified message"


def test_dispatch_folds_permission_via_stricter_verdict(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onToolUse": [
            HookConfig(type="bash", shell='echo \'{"permission": "allow"}\''),
            HookConfig(type="bash", shell='echo \'{"permission": "ask"}\''),
            HookConfig(type="bash", shell='echo \'{"permission": "allow"}\''),
        ],
    })
    result = HookDispatcher(process_config).dispatch(
        "onToolUse", _hook_input(tmp_path, hook="onToolUse", tool_name="Bash"),
        session_config=process_config.session)
    assert result.permission == "ask"


def test_dispatch_permission_stays_unset_when_every_handler_is_silent(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onToolUse": [HookConfig(type="bash", shell='echo \'{"message": "no opinion"}\'')],
    })
    result = HookDispatcher(process_config).dispatch(
        "onToolUse", _hook_input(tmp_path, hook="onToolUse", tool_name="Bash"),
        session_config=process_config.session)
    assert result.permission is None


def test_dispatch_filters_ontooluse_on_tool_name_not_event(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onToolUse": [
            HookConfig(
                type="bash", shell='echo \'{"message": "matched"}\'',
                filter=HookConfigFilter(matches="Bash")),
        ],
    })
    matching = HookDispatcher(process_config).dispatch(
        "onToolUse", HookInput(hook="onToolUse", workspace_root=str(tmp_path), tool_name="Bash"),
        session_config=process_config.session)
    assert matching.message == "matched"
    non_matching = HookDispatcher(process_config).dispatch(
        "onToolUse", HookInput(hook="onToolUse", workspace_root=str(tmp_path), tool_name="ReadFile"),
        session_config=process_config.session)
    assert non_matching.message is None


def test_dispatch_filters_onactivateskill_on_skill_name_not_event(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onActivateSkill": [
            HookConfig(
                type="bash", shell='echo \'{"message": "matched"}\'',
                filter=HookConfigFilter(matches="do-thing")),
        ],
    })
    matching = HookDispatcher(process_config).dispatch(
        "onActivateSkill",
        HookInput(hook="onActivateSkill", workspace_root=str(tmp_path), skill_name="do-thing"),
        session_config=process_config.session)
    assert matching.message == "matched"
    non_matching = HookDispatcher(process_config).dispatch(
        "onActivateSkill",
        HookInput(hook="onActivateSkill", workspace_root=str(tmp_path), skill_name="other-skill"),
        session_config=process_config.session)
    assert non_matching.message is None


def test_dispatch_filters_onsubmituserprompt_on_message_not_event(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onSubmitUserPrompt": [
            HookConfig(
                type="bash", shell='echo \'{"message": "matched"}\'',
                filter=HookConfigFilter(contains="deploy")),
        ],
    })
    matching = HookDispatcher(process_config).dispatch(
        "onSubmitUserPrompt",
        HookInput(hook="onSubmitUserPrompt", workspace_root=str(tmp_path), message="please deploy this"),
        session_config=process_config.session)
    assert matching.message == "matched"
    non_matching = HookDispatcher(process_config).dispatch(
        "onSubmitUserPrompt",
        HookInput(hook="onSubmitUserPrompt", workspace_root=str(tmp_path), message="please build this"),
        session_config=process_config.session)
    assert non_matching.message is None


def test_dispatch_uses_a_live_session_config_over_the_process_template(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onSessionEnd": [
            HookConfig(
                type="bash",
                shell='echo "{\\"message\\": \\"$WORKSPACE_ROOT\\"}"'),
        ],
    })
    other_root = tmp_path / "other"
    other_root.mkdir()
    live_session_config = SessionConfig(
        workspace=Workspace(path=other_root, trusted=True),
        read_dirs=DirRules(allow=[other_root]), write_dirs=DirRules(allow=[other_root]))
    result = HookDispatcher(process_config).dispatch(
        "onSessionEnd", _hook_input(tmp_path, hook="onSessionEnd", reason="SuspendSession"),
        session_config=live_session_config)
    assert result.message == str(other_root.resolve(strict=False))


def test_dispatch_raises_without_session_config_for_a_session_scoped_hook(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onToolUse": [HookConfig(type="bash", shell='echo \'{"message": "hi"}\'')],
    })
    with pytest.raises(ValueError, match="session_config is required"):
        HookDispatcher(process_config).dispatch(
            "onToolUse", _hook_input(tmp_path, hook="onToolUse", tool_name="Bash"))


def test_dispatch_does_not_raise_without_session_config_for_a_process_scoped_hook(
    tmp_path: Path,
) -> None:
    process_config = _process_config(tmp_path, {
        "onProcessStart": [HookConfig(type="bash", shell='echo \'{"message": "hi"}\'')],
    })
    result = HookDispatcher(process_config).dispatch("onProcessStart", _hook_input(tmp_path))
    assert result.message == "hi"


def test_dispatch_event_raises_without_session_config(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {})
    entries = [
        WorkspaceTrustChangedEventConfig(
            action=HookConfig(type="bash", shell='echo \'{"message": "hi"}\'')),
    ]
    with pytest.raises(ValueError, match="session_config is required"):
        HookDispatcher(process_config).dispatch_event(
            "WorkspaceTrustChanged", entries,
            EventInput(hook="WorkspaceTrustChanged", workspace_root=str(tmp_path), reason="TrustCommand"))


def test_dispatch_event_with_no_entries_returns_default_success(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {})
    result = HookDispatcher(process_config).dispatch_event(
        "WorkspaceTrustChanged", [],
        EventInput(hook="WorkspaceTrustChanged", workspace_root=str(tmp_path), reason="TrustCommand"),
        session_config=process_config.session)
    assert result.success is True
    assert result.message is None


def test_dispatch_event_runs_each_entrys_own_action_as_a_chain(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {})
    entries = [
        WorkspaceTrustChangedEventConfig(
            action=HookConfig(type="bash", shell='echo \'{"message": "first"}\'')),
        WorkspaceTrustChangedEventConfig(
            action=HookConfig(
                type="bash",
                shell=(
                    'python3 -c \'import sys, json; data = json.load(sys.stdin); '
                    'print(json.dumps({"message": data["message"] + "+second"}))\''))),
    ]
    result = HookDispatcher(process_config).dispatch_event(
        "WorkspaceTrustChanged", entries,
        EventInput(hook="WorkspaceTrustChanged", workspace_root=str(tmp_path), reason="TrustCommand"),
        session_config=process_config.session)
    assert result.message == "first+second"


def test_dispatch_event_filters_on_the_event_field(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {})
    entries = [
        WorkspaceTrustChangedEventConfig(
            action=HookConfig(
                type="bash", shell='echo \'{"message": "matched"}\'',
                filter=HookConfigFilter(matches="TrustCommand"))),
    ]
    matching = HookDispatcher(process_config).dispatch_event(
        "WorkspaceTrustChanged", entries,
        EventInput(hook="WorkspaceTrustChanged", workspace_root=str(tmp_path), reason="TrustCommand"),
        session_config=process_config.session)
    assert matching.message == "matched"
    non_matching = HookDispatcher(process_config).dispatch_event(
        "WorkspaceTrustChanged", entries,
        EventInput(hook="WorkspaceTrustChanged", workspace_root=str(tmp_path), reason="AcpTrustWorkspace"),
        session_config=process_config.session)
    assert non_matching.message is None
