# © Copyright 2026 Aaron Kimball
"""Shared fixtures/helpers for the klorb.tui.app.ReplApp test tree."""


import asyncio
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch

import fixtures.sample_tools as sample_tools_package
import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Static

from klorb.api_provider import ProviderResponse
from klorb.message import Message, MessageRole, ToolCallRequest
from klorb.process_config import ProcessConfig
from klorb.session import PermissionAskContext, PermissionDecision, Session, SessionConfig
from klorb.tools.registry import ToolRegistry
from klorb.tui.app import ReplApp
from klorb.tui.constants import HISTORY_ID, PROMPT_INPUT_ID
from klorb.tui.panels.permission_ask_panel import PermissionAskPanel
from klorb.tui.widgets.palette import PALETTE_PREFIX, PaletteOption, PromptPalette
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.workspace import TrustManager, Workspace
from klorb.workspace import input_history as input_history_module

TEST_SESSION_ID = "test-session-id"


def _session(provider: MagicMock, model: str = "some/model") -> Session:
    return Session(SessionConfig(model=model), provider=provider, session_id=TEST_SESSION_ID)


def _session_with_tools(
    provider: MagicMock, config: SessionConfig, process_config: ProcessConfig | None = None,
) -> Session:
    tool_registry = ToolRegistry(process_config or ProcessConfig(), config, package=sample_tools_package)
    return Session(
        config, provider=provider, session_id=TEST_SESSION_ID, tool_registry=tool_registry,
        process_config=process_config)


def _palette_hit_texts(palette: PromptPalette) -> set[str]:
    """The canonical `text` of every hit currently rendered in `palette`'s rows."""
    options = palette._options
    assert all(isinstance(option, PaletteOption) for option in options)
    return {str(option.hit.text) for option in options if isinstance(option, PaletteOption)}


@pytest.fixture(autouse=True)
def _user_config_present(tmp_path: Path) -> Iterator[None]:
    """Make `user_config_path()` resolve to an existing file by default, so `ReplApp.on_mount`'s
    "config file not found" notice (see `CONFIG_MISSING_MESSAGE`) doesn't leak an extra
    `Static` into every other test's history assertions. The tests that specifically exercise
    the notice re-patch `klorb.tui.mixins.key_actions.user_config_path` themselves, overriding
    this.
    """
    config_path = tmp_path / "klorb-config.json"
    config_path.write_text("{}", encoding="utf-8")
    with patch("klorb.tui.mixins.key_actions.user_config_path", return_value=config_path):
        yield


@pytest.fixture(autouse=True)
def stub_force_exit() -> Iterator[MagicMock]:
    """Neutralize `ReplApp._force_exit`'s `os._exit` for the whole TUI suite: every `ReplApp`
    starts a real `LivenessWatchdog`, and a test that stalled the event loop long enough (or a
    double-Ctrl+C test) would otherwise call the real `force_exit` and terminate the pytest
    process. Patching the name `ReplApp` imported keeps the wiring under test while making the
    exit a no-op the double-Ctrl+C tests can also assert against."""
    with patch("klorb.tui.mixins.key_actions.force_exit") as mock_force_exit:
        yield mock_force_exit


@pytest.fixture(autouse=True)
def stub_session_naming() -> Iterator[MagicMock]:
    """Neutralize the first-turn session-naming classifier (`klorb.session_naming.
    generate_session_name`) for the whole TUI suite: patched to return `None` (today's
    "classifier unavailable" fallback) so submitting a prompt in an ordinary test doesn't send
    an extra request through the test's `ApiProvider` mock and consume a queued response meant
    for the turn itself. `tests/tui/mixins/test_prompt_submission.py`'s own naming-specific
    tests re-patch this themselves, overriding this default."""
    with patch(
        "klorb.tui.mixins.prompt_submission.generate_session_name", return_value=None,
    ) as mock_generate_session_name:
        yield mock_generate_session_name


async def _invoke_clear_session(pilot: Pilot[None]) -> None:
    """Select "Clear session" from the inline palette (see
    docs/specs/command-palette-from-prompt.md), mirroring how a real user reaches it now that
    the bare `/clear` prompt text is no longer special-cased.

    Sets the prompt text directly rather than typing `>clear` key-by-key: each simulated
    keystroke costs a real ~20-40ms of wall clock (`Pilot.press`'s `wait_for_idle` calls), which
    adds up across the many tests that call this helper, and setting `.text` still fires the
    same `TextArea.Changed` -> `_refresh_palette` path a real keystroke would.
    """
    prompt_input = pilot.app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
    prompt_input.text = f"{PALETTE_PREFIX}clear"
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


async def _wait_until(pilot: Pilot[None], predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll `predicate` via repeated `pilot.pause()` calls until it's true.

    Some scroll effects (e.g. `VerticalScroll.scroll_end()`/`scroll_home()` with the default
    `immediate=False`) are deferred until after a layout refresh rather than applying
    synchronously, so a single `pilot.pause()` isn't reliably enough to observe their result.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"Timed out after {timeout}s waiting for condition")
        await pilot.pause()


def _focused_id(app: ReplApp) -> str | None:
    focused = app.focused
    return focused.id if focused is not None else None


def _reply(content: str = "model reply") -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content,
            role="assistant",
            num_tokens=1,
            processing_state="complete",
            timestamp=datetime.now(),
        ),
        prompt_tokens=1,
    )


def _tool_call_reply(calls: list[tuple[str, str, str]]) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content="",
            role="assistant",
            num_tokens=1,
            processing_state="complete",
            timestamp=datetime.now(),
            finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id=id_, name=name, arguments=args) for id_, name, args in calls],
        ),
        prompt_tokens=1,
    )


def _risk_report_reply(assessments: list[tuple[str, int, str, list[str]]]) -> ProviderResponse:
    """A `ProviderResponse` whose content is a `CommandRiskReport`-shaped JSON payload, one
    `ItemRiskAssessment` per `(item_id, risk_score, rationale, suggested_pattern)` tuple -- for
    a mock `ApiProvider.send_prompt` standing in for `klorb.permissions.risk_classifier`'s
    classifier call."""
    return _reply(json.dumps({
        "overall_risk_score": max((score for _, score, _, _ in assessments), default=0),
        "overall_rationale": "test overall rationale",
        "items": [
            {
                "item_id": item_id, "risk_score": score, "rationale": rationale,
                "suggested_pattern": pattern,
            }
            for item_id, score, rationale, pattern in assessments
        ],
    }))


def _notice_texts(app: ReplApp) -> list[str]:
    history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
    notices = history.query(".notice")
    assert all(isinstance(widget, Static) for widget in notices)
    return [str(widget.content) for widget in notices if isinstance(widget, Static)]


def _command_ask_ctx(
    command_text: str, *, reason: str = "some reason", is_compound: bool = False,
    item_command_text: str | None = None, intent: str | None = None,
) -> PermissionAskContext:
    """A bash-command-ask context (no `path`, matching a structural item's shape), for testing
    the "Run command" header/command-preview path -- see `_ask_ctx` for the file-tool ("Read
    file"/"Write file" header) counterpart."""
    return PermissionAskContext(
        command_text=command_text, resource_description=reason, is_compound=is_compound,
        item_command_text=item_command_text, intent=intent)


class _PermissionAskTestApp(App[None]):
    """Minimal standalone harness for driving a real `PermissionAskPanel` through Textual's
    `Pilot`, without needing a full `ReplApp`/`Session` -- the "+"/click-to-expand behavior only
    exists on `PermissionAskPanel` and `ExpandedCommandScreen` themselves, so there's nothing
    session- or tool-call-shaped for a heavier harness to add. `decision` records whatever
    `PermissionAskPanel` is dismissed with, for tests that need to confirm Enter actually
    reached `action_confirm` rather than just checking mounted-widget state."""

    def __init__(self, ctx: PermissionAskContext) -> None:
        super().__init__()
        self._ctx = ctx
        self.decision: PermissionDecision | None = None

    def compose(self) -> ComposeResult:
        yield Vertical()

    async def on_mount(self) -> None:
        panel = PermissionAskPanel(self._ctx, on_dismiss=self._set_decision)
        await self.query_one(Vertical).mount(panel)

    def _set_decision(self, decision: PermissionDecision) -> None:
        self.decision = decision


async def _complete_turn(pilot: Pilot[None], app: ReplApp) -> None:
    """Wait for the in-flight model turn to finish and the input box to re-enable."""
    await app.workers.wait_for_complete()
    await pilot.pause()


def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `klorb.workspace.input_history` (and the `TrustManager`) at an empty
    `$KLORB_DATA_DIR` under `tmp_path` and return it, so persistence tests never touch the
    developer's own `~/.local/share/klorb/`."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(input_history_module, "KLORB_DATA_DIR", data_dir)
    return data_dir


def _process_config_for_workspace(workspace: Workspace, model: str = "some/model") -> ProcessConfig:
    return ProcessConfig(session=SessionConfig(model=model, workspace=workspace))


def _repl_app_for_workspace(
    workspace: Workspace, trust_manager: TrustManager | None, model: str = "some/model",
) -> ReplApp:
    process_config = _process_config_for_workspace(workspace, model)
    session = Session(
        process_config.session.model_copy(), provider=MagicMock(), session_id=TEST_SESSION_ID,
        process_config=process_config)
    return ReplApp(session=session, process_config=process_config, trust_manager=trust_manager)


def _sample_message(content: str = "hi", role: MessageRole = "user") -> Message:
    return Message(
        content=content, role=role, num_tokens=1, processing_state="complete",
        timestamp=datetime(2026, 7, 12, 0, 0, 0))
