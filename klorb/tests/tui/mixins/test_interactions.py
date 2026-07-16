# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.mixins.interactions.InteractionsMixin."""

import asyncio
import json
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.containers import Grid as GridContainer
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.widgets import Input, Markdown, Static
from tui.conftest import (
    _command_ask_ctx,
    _focused_id,
    _PermissionAskTestApp,
    _reply,
    _risk_report_reply,
    _session,
    _session_with_tools,
    _tool_call_reply,
    _wait_until,
)

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskItem
from klorb.process_config import ProcessConfig
from klorb.session import (
    AskUserQuestionsAnswer,
    AskUserQuestionsItemContext,
    PermissionAskContext,
    PermissionDecision,
    SessionConfig,
)
from klorb.tools.ask.common import QuestionOption
from klorb.tui.app import ReplApp
from klorb.tui.constants import HISTORY_ID, INTERACTION_PANEL_ID, PROMPT_INPUT_ID
from klorb.tui.panels.ask_user_questions_panel import AskUserQuestionsPanel
from klorb.tui.panels.permission_ask_panel import (
    _MAX_COMMAND_PREVIEW_LINES,
    _MAX_SECONDARY_TEXT_LINES,
    PERMISSION_ASK_COMMAND_ID,
    PERMISSION_ASK_DETAIL_ID,
    PERMISSION_ASK_GRANTED_ID,
    PERMISSION_ASK_GRID_ID,
    PERMISSION_ASK_HEADER_ID,
    PERMISSION_ASK_INPUT_ID,
    PERMISSION_ASK_INTENT_ID,
    PERMISSION_ASK_MORE_ID,
    PERMISSION_ASK_RATIONALE_ID,
    PERMISSION_ASK_RISK_BADGE_ID,
    ExpandedCommandScreen,
    PermissionAskPanel,
)
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.tool_call_widgets import ToolCallLimitScreen
from klorb.workspace import Workspace


async def test_entering_interaction_mode_disables_mutes_and_collapses_the_prompt_input() -> None:
    """`_enter_interaction_mode` is what `ReplApp` wraps every permission ask and
    ask-user-questions panel in: while active, the prompt input is disabled, visually muted,
    and collapsed back down to its default single-row height even if it was holding an
    in-progress multi-line draft -- the draft text itself is left untouched underneath."""
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "line one\nline two\nline three"
        await pilot.pause()
        assert prompt_input.outer_size.height > 1

        panel_container = app._enter_interaction_mode()
        await pilot.pause()

        assert panel_container.id == INTERACTION_PANEL_ID
        assert prompt_input.disabled
        assert prompt_input.has_class("interaction-active")
        assert prompt_input.outer_size.height == 1
        assert prompt_input.text == "line one\nline two\nline three"


async def test_exiting_interaction_mode_unmutes_but_leaves_the_prompt_input_disabled() -> None:
    """The other half of `test_entering_interaction_mode_disables_mutes_and_collapses_the_prompt_input`:
    `_exit_interaction_mode` un-mutes and expands the prompt input back to fit its (untouched)
    draft text once a panel is dismissed, but deliberately leaves it *disabled* -- an interaction
    panel only appears part-way through a still-running turn, so the input must stay locked until
    `_finish_turn` re-enables it, rather than being re-enabled here and letting the user launch a
    second concurrent turn whose panels would stack onto this one's."""
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "line one\nline two\nline three"
        await pilot.pause()

        app._enter_interaction_mode()
        await pilot.pause()
        app._exit_interaction_mode()
        await pilot.pause()

        assert prompt_input.disabled is True
        assert prompt_input.has_class("interaction-active") is False
        assert prompt_input.outer_size.height > 1
        assert prompt_input.text == "line one\nline two\nline three"


def _question_ctx(header: str) -> AskUserQuestionsItemContext:
    return AskUserQuestionsItemContext(
        header=header, question=f"{header}?",
        options=[QuestionOption("Yes", None, False), QuestionOption("No", None, False)],
        index=1, total=1)


async def test_two_overlapping_interaction_panels_never_mount_at_once() -> None:
    """`_interaction_lock` serializes the panel lifecycle so a second confirmation that somehow
    starts while a first is still awaiting the user queues behind it rather than stacking a
    second panel into `#interaction-panel`. Guards the "stacked approval panels" bug at the
    structural level, independent of the (separate) fix that keeps the prompt input disabled for
    a whole turn so a second concurrent turn can't be launched in the first place."""
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        first = asyncio.create_task(app._confirm_ask_user_questions(_question_ctx("First")))
        second = asyncio.create_task(app._confirm_ask_user_questions(_question_ctx("Second")))

        await _wait_until(pilot, lambda: bool(app.query(AskUserQuestionsPanel)))
        # Only the first panel is mounted; the second confirmation is blocked on the lock.
        assert len(app.query(AskUserQuestionsPanel)) == 1
        assert app.query_one(AskUserQuestionsPanel)._ask_ctx.header == "First"

        app.query_one(AskUserQuestionsPanel).dismiss(AskUserQuestionsAnswer(answer="Yes"))
        await first
        await _wait_until(
            pilot,
            lambda: bool(app.query(AskUserQuestionsPanel))
            and app.query_one(AskUserQuestionsPanel)._ask_ctx.header == "Second")
        # The first panel is gone; exactly one (the second) is now up in its place.
        assert len(app.query(AskUserQuestionsPanel)) == 1

        app.query_one(AskUserQuestionsPanel).dismiss(AskUserQuestionsAnswer(answer="No"))
        await second


async def test_tool_call_limit_modal_yes_continues_the_turn() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", max_tool_calls_per_turn=0)
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ToolCallLimitScreen)

        await pilot.click("#tool-call-limit-yes")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert len(app.screen_stack) == 1
        response_widget = app.query_one(f"#{HISTORY_ID}", VerticalScroll).children[-1]
        assert isinstance(response_widget, Markdown)
        assert response_widget.source == "final answer"


async def test_tool_call_limit_modal_no_shows_error() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = SessionConfig(model="some/model", max_tool_calls_per_turn=0)
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ToolCallLimitScreen)

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert len(app.screen_stack) == 1
        error_widget = app.query_one(f"#{HISTORY_ID}", VerticalScroll).children[-1]
        assert isinstance(error_widget, Static)
        assert "0 tool call" in str(error_widget.render())


async def test_tool_call_limit_modal_arrow_keys_move_focus_between_buttons() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = SessionConfig(model="some/model", max_tool_calls_per_turn=0)
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ToolCallLimitScreen)

        assert _focused_id(app) == "tool-call-limit-yes"
        await pilot.press("right")
        assert _focused_id(app) == "tool-call-limit-no"
        await pilot.press("left")
        assert _focused_id(app) == "tool-call-limit-yes"

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()


# --- tool call rendering ---


def _ask_permission_call(id_: str, path: Path, *, is_write: bool = True) -> tuple[str, str, str]:
    return id_, "ask_permission", json.dumps({"path": str(path), "is_write": is_write})


# --- PermissionAskPanel (unit-level, mirroring ThinkingEffortScreen's test style) ---


def _ask_ctx(tmp_path: Path, is_write: bool = True) -> PermissionAskContext:
    target = tmp_path / "f.txt"
    return PermissionAskContext(path=target, is_write=is_write, resource_description=f"write to {target}")


def _find_child(container: object, widget_id: str) -> object:
    return next(
        widget for widget in container._pending_children  # type: ignore[attr-defined]
        if widget.id == widget_id)


def test_permission_ask_screen_names_the_granted_directory(tmp_path: Path) -> None:
    screen = PermissionAskPanel(_ask_ctx(tmp_path), granted_paths=[tmp_path])

    container = next(iter(screen.compose()))
    granted = _find_child(container, PERMISSION_ASK_GRANTED_ID)

    assert isinstance(granted, Static)
    assert str(tmp_path) in str(granted.render())


def test_granted_line_keeps_a_bracketed_pattern_verbatim_as_a_styled_span() -> None:
    """Regression test: the granted-scope line accents just the *pattern span* within a line of
    fixed prose, so it can't lean on a whole-widget CSS style the way the command preview does --
    it must build a `Content` with an explicit styled span. It must NOT instead wrap the pattern
    in inline markup + `textual.markup.escape()`: escaping is less conservative than the parser,
    so a pattern like `echo [$HOME]` slips through and is silently dropped, misreporting exactly
    what a persistent Allow would grant. See
    docs/adrs/style-arbitrary-text-spans-with-content-not-escaped-markup.md."""
    screen = PermissionAskPanel(
        _command_ask_ctx("echo [$HOME]"), granted_command_patterns=[["echo", "[$HOME]"]])

    container = next(iter(screen.compose()))
    granted = _find_child(container, PERMISSION_ASK_GRANTED_ID)

    assert isinstance(granted, Static)
    rendered = granted.render()
    # The pattern survives verbatim (not swallowed) ...
    assert isinstance(rendered, Content)
    assert rendered.plain == "Any persistent Allow choice below grants: echo [$HOME]"
    # ... and is the styled span, set off from the surrounding prose.
    assert any(
        rendered.plain[span.start:span.end] == "echo [$HOME]" and "bold" in str(span.style)
        for span in rendered.spans)


def test_permission_ask_screen_grid_has_eight_cells_plus_a_trailing_other_cell(
    tmp_path: Path,
) -> None:
    screen = PermissionAskPanel(_ask_ctx(tmp_path), granted_paths=[tmp_path])

    container = next(iter(screen.compose()))
    grid = _find_child(container, PERMISSION_ASK_GRID_ID)

    assert isinstance(grid, GridContainer)
    labels = tuple(str(cell.render()) for cell in grid._pending_children)
    assert labels == (
        "Allow — Once", "Deny — Once",
        "Allow — This session", "Deny — This session",
        "Allow — Always, this workspace", "Deny — Always, this workspace",
        "Allow — Always, for me", "Deny — Always, for me",
        "Other...",
    )


def test_permission_ask_screen_shows_the_agents_stated_intent(tmp_path: Path) -> None:
    screen = PermissionAskPanel(_command_ask_ctx("grep -n _wait_until tests/", intent="List call sites"))

    container = next(iter(screen.compose()))
    intent_static = _find_child(container, PERMISSION_ASK_INTENT_ID)

    assert isinstance(intent_static, Static)
    assert str(intent_static.render()) == "Intent: List call sites"


def test_permission_ask_screen_omits_intent_line_when_unset(tmp_path: Path) -> None:
    screen = PermissionAskPanel(_command_ask_ctx("echo hi"))

    container = next(iter(screen.compose()))
    with pytest.raises(StopIteration):
        _find_child(container, PERMISSION_ASK_INTENT_ID)


def test_permission_ask_screen_header_says_run_command_when_command_text_is_set(
    tmp_path: Path,
) -> None:
    screen = PermissionAskPanel(_command_ask_ctx("echo hi"))

    container = next(iter(screen.compose()))
    header = _find_child(container, PERMISSION_ASK_HEADER_ID)

    assert isinstance(header, Static)
    assert str(header.render()) == "Permission requested: Run command"


@pytest.mark.parametrize(("is_write", "expected_kind"), [(True, "Write file"), (False, "Read file")])
def test_permission_ask_screen_header_names_read_or_write_when_path_is_set(
    tmp_path: Path, is_write: bool, expected_kind: str,
) -> None:
    screen = PermissionAskPanel(_ask_ctx(tmp_path, is_write=is_write))

    container = next(iter(screen.compose()))
    header = _find_child(container, PERMISSION_ASK_HEADER_ID)

    assert isinstance(header, Static)
    assert str(header.render()) == f"Permission requested: {expected_kind}"


def test_permission_ask_screen_shows_the_full_short_command_with_no_more_indicator(
    tmp_path: Path,
) -> None:
    screen = PermissionAskPanel(_command_ask_ctx("echo hi"))

    container = next(iter(screen.compose()))
    command_static = _find_child(container, PERMISSION_ASK_COMMAND_ID)

    assert isinstance(command_static, Static)
    assert str(command_static.render()) == "echo hi"
    with pytest.raises(StopIteration):
        _find_child(container, PERMISSION_ASK_MORE_ID)


def test_permission_ask_screen_truncates_a_long_command_with_a_more_indicator(
    tmp_path: Path,
) -> None:
    long_command = "\n".join(f"line {i}" for i in range(1, 21))  # 20 lines
    screen = PermissionAskPanel(_command_ask_ctx(long_command))

    container = next(iter(screen.compose()))
    command_static = _find_child(container, PERMISSION_ASK_COMMAND_ID)
    more = _find_child(container, PERMISSION_ASK_MORE_ID)

    assert isinstance(command_static, Static)
    assert str(command_static.render()) == "\n".join(f"line {i}" for i in range(1, 7))
    assert isinstance(more, Static)
    assert str(more.render()) == "[more...]"


def test_permission_ask_screen_truncates_a_long_single_line_when_wrap_width_is_given(
    tmp_path: Path,
) -> None:
    """A single very long line with no explicit newline still gets truncated (and its
    `[more...]` indicator shown) once `preview_wrap_width` is given -- otherwise, once
    actually rendered, it would soft-wrap to many more rows than `_MAX_COMMAND_PREVIEW_LINES`
    and push the grid below it off screen. `ReplApp._confirm_permission_ask` is the only real
    caller that ever supplies this."""
    long_line = " ".join(f"word{i}" for i in range(200))  # one very long line, no "\n" at all
    screen = PermissionAskPanel(_command_ask_ctx(long_line), preview_wrap_width=40)

    container = next(iter(screen.compose()))
    command_static = _find_child(container, PERMISSION_ASK_COMMAND_ID)
    more = _find_child(container, PERMISSION_ASK_MORE_ID)

    assert isinstance(command_static, Static)
    rendered = str(command_static.render())
    assert rendered != long_line
    assert len(rendered) < len(long_line)
    assert isinstance(more, Static)
    assert str(more.render()) == "[more...]"


def test_permission_ask_screen_does_not_truncate_a_long_single_line_with_no_wrap_width(
    tmp_path: Path,
) -> None:
    """Without `preview_wrap_width` (the default -- every caller that never mounts this panel
    into a real, sized terminal, e.g. every other unit test in this file), truncation only
    counts explicit newlines, matching the line-count-only behavior every other test here
    already assumes."""
    long_line = " ".join(f"word{i}" for i in range(200))
    screen = PermissionAskPanel(_command_ask_ctx(long_line))

    container = next(iter(screen.compose()))
    command_static = _find_child(container, PERMISSION_ASK_COMMAND_ID)

    assert isinstance(command_static, Static)
    assert str(command_static.render()) == long_line
    with pytest.raises(StopIteration):
        _find_child(container, PERMISSION_ASK_MORE_ID)


def test_permission_ask_screen_shows_more_indicator_for_a_short_compound_command(
    tmp_path: Path,
) -> None:
    """A short `foo && bar` still gets `[more...]` when `is_compound` is set, even though the
    full command already fits on screen without truncation -- see
    docs/adrs/always-show-more-indicator-for-compound-command-ask-items.md."""
    screen = PermissionAskPanel(_command_ask_ctx(
        "echo hi && echo bye", reason="run command: echo hi", is_compound=True))

    container = next(iter(screen.compose()))
    command_static = _find_child(container, PERMISSION_ASK_COMMAND_ID)
    more = _find_child(container, PERMISSION_ASK_MORE_ID)

    assert isinstance(command_static, Static)
    assert str(command_static.render()) == "echo hi && echo bye"
    assert isinstance(more, Static)
    assert str(more.render()) == "[more...]"


def test_permission_ask_screen_preview_shows_the_items_own_command_not_the_full_compound(
    tmp_path: Path,
) -> None:
    """Regression test for the compound-command permission-ask bug: `echo $SHELL; echo $HOME`
    previously showed the identical full command_text in every item's preview, giving the user no
    way to tell which approval request was about which command -- the preview must show this
    item's own item_command_text instead. See docs/adrs/
    permission-ask-item-shows-its-own-command-text-not-the-full-compound.md."""
    screen = PermissionAskPanel(_command_ask_ctx(
        "echo $SHELL; echo $HOME", is_compound=True, item_command_text="echo $SHELL"))

    container = next(iter(screen.compose()))
    command_static = _find_child(container, PERMISSION_ASK_COMMAND_ID)

    assert isinstance(command_static, Static)
    assert str(command_static.render()) == "echo $SHELL"


def test_permission_ask_screen_preview_falls_back_to_command_text_with_no_item_command_text(
    tmp_path: Path,
) -> None:
    screen = PermissionAskPanel(_command_ask_ctx("echo hi", item_command_text=None))

    container = next(iter(screen.compose()))
    command_static = _find_child(container, PERMISSION_ASK_COMMAND_ID)

    assert isinstance(command_static, Static)
    assert str(command_static.render()) == "echo hi"


# --- PermissionAskPanel: command expansion, mounted (needs a real App to test key/click) ---


async def test_plus_key_opens_expanded_command_screen_with_the_full_untruncated_text() -> None:
    long_command = "\n".join(f"line {i}" for i in range(1, 21))
    app = _PermissionAskTestApp(_command_ask_ctx(long_command))

    async with app.run_test() as pilot:
        await pilot.press("plus")
        await pilot.pause()

        assert isinstance(app.screen, ExpandedCommandScreen)
        assert str(app.screen.query_one(Static).render()) == long_command

        await pilot.press("escape")
        await pilot.pause()

        app.query_one(PermissionAskPanel)


async def test_a_command_with_brackets_mounts_without_a_markup_error() -> None:
    """Regression test: a command whose text contains `[...]` (e.g. a Python list comprehension)
    must render verbatim, not be parsed as Textual content markup. Every arbitrary-text `Static`
    in the panel is built with `markup=False` for this reason -- otherwise the `[` opens a markup
    tag and the compositor raises `MarkupError` the moment it reflows the panel (parsing happens
    at reflow, not at `Static.render()`, which is why a `.compose()`-only unit test wouldn't catch
    it). The whole ask path -- command preview, expanded screen, and `resource_description`
    detail -- is exercised by expanding the command and mounting it. See
    docs/adrs/permission-ask-screen-shows-a-header-command-preview-and-detail.md."""
    bracketed = "python -c \"print([m for m in modes if m=='selection' or m=='text' or m=='cursor'])\""
    app = _PermissionAskTestApp(_command_ask_ctx(bracketed, is_compound=True, reason=bracketed))

    async with app.run_test() as pilot:
        panel = app.query_one(PermissionAskPanel)
        command_static = panel.query_one(f"#{PERMISSION_ASK_COMMAND_ID}", Static)
        assert str(command_static.render()) == bracketed

        await pilot.press("plus")
        await pilot.pause()

        assert isinstance(app.screen, ExpandedCommandScreen)
        assert str(app.screen.query_one(Static).render()) == bracketed


async def test_a_path_with_brackets_mounts_without_a_markup_error(tmp_path: Path) -> None:
    """Regression counterpart to the command case for the file-tool path preview: a filesystem
    path containing `[` must render verbatim rather than be parsed as content markup."""
    bracketed_path = tmp_path / "weird[dir]" / "f.txt"
    ctx = PermissionAskContext(
        path=bracketed_path, is_write=True, resource_description=f"write to {bracketed_path}")
    app = _PermissionAskTestApp(ctx)

    async with app.run_test():
        panel = app.query_one(PermissionAskPanel)
        command_static = panel.query_one(f"#{PERMISSION_ASK_COMMAND_ID}", Static)
        assert str(command_static.render()) == str(bracketed_path)


async def test_more_indicator_is_never_focused_so_it_cannot_steal_enter_from_the_grid() -> None:
    """Regression test: `_MoreIndicator` briefly had `can_focus=True` with its own Enter
    binding, and mounting a `PermissionAskPanel` focuses the panel itself (see
    `PermissionAskPanel.on_mount`) -- if the indicator were focusable instead, it would grab
    focus (and every subsequent Enter keystroke) the moment the panel mounted, with no click
    needed, making the grid's Allow/Deny selection unconfirmable by keyboard whenever a command
    was long enough to truncate. See
    docs/adrs/permission-ask-screen-shows-a-header-command-preview-and-detail.md."""
    long_command = "\n".join(f"line {i}" for i in range(1, 21))
    app = _PermissionAskTestApp(_command_ask_ctx(long_command))

    async with app.run_test() as pilot:
        panel = app.query_one(PermissionAskPanel)
        assert app.focused is panel

        await pilot.click(f"#{PERMISSION_ASK_MORE_ID}")
        await pilot.pause()
        assert isinstance(app.screen, ExpandedCommandScreen)

        await pilot.press("escape")
        await pilot.pause()
        app.query_one(PermissionAskPanel)
        assert app.focused is panel

        await pilot.press("enter")
        await pilot.pause()
        assert app.decision == PermissionDecision(action="allow", scope="once")


async def test_every_body_static_is_height_capped_so_a_long_line_cannot_push_the_grid_off_screen(
) -> None:
    """Belt-and-suspenders regression: every variable-content body `Static` -- the intent line, the
    command preview, the risk rationale, the per-item detail, and the granted-scope line -- is
    clipped to a bounded number of rows at mount, so no single very long line (which soft-wraps to
    arbitrarily many rows once rendered) can grow the panel tall enough to push the decision grid
    off the bottom of the screen. Rendered here *without* `preview_wrap_width`, so the command
    preview's own content-level truncation is deliberately not in play and only the height cap
    protects it. Uncapped, the intent (~28 rows), command and detail (~40 rows each), rationale
    (~28 rows) and granted line together dwarf even this tall test terminal; capped, they sum well
    under it and the grid stays fully on screen. See `PermissionAskPanel._cap_body_static_heights`,
    docs/adrs/cap-every-permission-ask-body-static-height.md."""
    huge = "x" * 4000
    ctx = PermissionAskContext(
        command_text="python3 -c " + huge, is_compound=True,
        item_command_text="python3 -c " + huge,
        intent="run an inline one-liner that does a whole lot of things " * 40,
        resource_description="run command: python3 -c " + huge)

    class _Harness(App[None]):
        def compose(self) -> ComposeResult:
            yield Vertical()

        async def on_mount(self) -> None:
            await self.query_one(Vertical).mount(PermissionAskPanel(
                ctx, risk_score=8,
                risk_rationale="this rewrites files in place and is hard to undo " * 40,
                granted_command_patterns=[["python3", huge]]))

    app = _Harness()
    async with app.run_test(size=(100, 60)) as pilot:
        await pilot.pause()
        for capped_id in (
            PERMISSION_ASK_INTENT_ID, PERMISSION_ASK_DETAIL_ID,
            PERMISSION_ASK_RATIONALE_ID, PERMISSION_ASK_GRANTED_ID,
        ):
            assert app.query_one(f"#{capped_id}", Static).region.height <= _MAX_SECONDARY_TEXT_LINES
        command = app.query_one(f"#{PERMISSION_ASK_COMMAND_ID}", Static)
        assert command.region.height <= _MAX_COMMAND_PREVIEW_LINES
        grid = app.query_one(f"#{PERMISSION_ASK_GRID_ID}", GridContainer)
        assert 0 <= grid.region.y
        assert grid.region.bottom <= app.size.height


@pytest.mark.parametrize(("column", "row", "expected_action", "expected_scope"), [
    (0, 0, "allow", "once"), (1, 0, "deny", "once"),
    (0, 1, "allow", "session"), (1, 3, "deny", "homedir"),
])
def test_action_confirm_dismisses_with_the_selected_grid_cell(
    tmp_path: Path, column: int, row: int,
    expected_action: Literal["allow", "deny"],
    expected_scope: Literal["once", "session", "workspace", "homedir"],
) -> None:
    screen = PermissionAskPanel(_ask_ctx(tmp_path), granted_paths=[tmp_path])
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    screen._column = column
    screen._row = row

    screen.action_confirm()

    screen.dismiss.assert_called_once_with(
        PermissionDecision(action=expected_action, scope=expected_scope))


def test_action_move_column_wraps_around(tmp_path: Path) -> None:
    screen = PermissionAskPanel(_ask_ctx(tmp_path), granted_paths=[tmp_path])
    screen._refresh_selection = MagicMock()  # type: ignore[method-assign]

    screen.action_move_column(-1)

    assert screen._column == 1  # wrapped from column 0 ("allow") to the last column ("deny")


def test_action_move_row_wraps_around(tmp_path: Path) -> None:
    screen = PermissionAskPanel(_ask_ctx(tmp_path), granted_paths=[tmp_path])
    screen._refresh_selection = MagicMock()  # type: ignore[method-assign]

    screen.action_move_row(-1)

    assert screen._row == 4  # wrapped from row 0 ("once") past "homedir" to the Other row


def test_action_move_row_down_four_times_from_once_reaches_the_other_row(tmp_path: Path) -> None:
    screen = PermissionAskPanel(_ask_ctx(tmp_path), granted_paths=[tmp_path])
    screen._refresh_selection = MagicMock()  # type: ignore[method-assign]

    for _ in range(4):
        screen.action_move_row(1)

    assert screen._row == 4


def test_action_other_reveals_input_instead_of_dismissing(tmp_path: Path) -> None:
    screen = PermissionAskPanel(_ask_ctx(tmp_path), granted_paths=[tmp_path])
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    screen._reveal_other_input = MagicMock()  # type: ignore[method-assign]

    screen.action_other()

    screen._reveal_other_input.assert_called_once()
    screen.dismiss.assert_not_called()


def test_action_confirm_on_other_row_reveals_input_instead_of_dismissing(tmp_path: Path) -> None:
    screen = PermissionAskPanel(_ask_ctx(tmp_path), granted_paths=[tmp_path])
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    screen._reveal_other_input = MagicMock()  # type: ignore[method-assign]
    screen._row = 4

    screen.action_confirm()

    screen._reveal_other_input.assert_called_once()
    screen.dismiss.assert_not_called()


def test_on_input_submitted_dismisses_with_a_once_scoped_denial_and_the_text(tmp_path: Path) -> None:
    screen = PermissionAskPanel(_ask_ctx(tmp_path), granted_paths=[tmp_path])
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    event = MagicMock(value="use /tmp instead")

    screen.on_input_submitted(event)

    screen.dismiss.assert_called_once_with(
        PermissionDecision(action="deny", scope="once", other_text="use /tmp instead"))


def test_action_decline_dismisses_with_a_once_scoped_denial(tmp_path: Path) -> None:
    screen = PermissionAskPanel(_ask_ctx(tmp_path), granted_paths=[tmp_path])
    screen.dismiss = MagicMock()  # type: ignore[method-assign]

    screen.action_decline()

    screen.dismiss.assert_called_once_with(PermissionDecision(action="deny", scope="once"))


# --- PermissionAskPanel end-to-end through ReplApp ---


async def test_permission_ask_modal_appears_for_an_ask_tool_call(tmp_path: Path) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", tmp_path / "f.txt")]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please touch a file"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))

        panel = app.query_one(PermissionAskPanel)
        assert panel.parent is app.query_one(f"#{INTERACTION_PANEL_ID}", Vertical)
        assert app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).disabled


async def test_permission_ask_modal_escape_denies_and_shows_error(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please touch a file"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))
        app.query_one(PermissionAskPanel)

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert not app.query(PermissionAskPanel)
        assert not app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).disabled
        tool_response = next(m for m in app._session.messages if m.role == "tool_response")
        assert "Permission denied" in str(tool_response.content)
        assert config.read_dirs == DirRules()
        assert config.write_dirs == DirRules()


async def test_release_pending_interaction_unblocks_a_parked_confirm() -> None:
    """The lever behind both the "model hangs" and "can't quit" fixes: while an interaction panel
    is awaiting the user, `_release_pending_interaction` resolves its decision future with a safe
    default (deny/once here), releasing whatever is blocked on it -- a real turn's worker thread is
    parked in `App.call_from_thread` on exactly this future. `_signal_turn_cancellation` fires it,
    and the callback is cleared once the confirm returns. See
    docs/adrs/unblock-worker-thread-before-teardown-so-quit-cannot-hang.md."""
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        task = asyncio.ensure_future(app._confirm_permission_ask(_command_ask_ctx("echo hi")))
        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))
        # Narrow a local rather than the instance attribute: asserting `is not None` directly on
        # `app._release_pending_interaction` would leave mypy believing it stays non-None across the
        # `await` below, making the later `is None` check (and everything after it) unreachable.
        pending_release = app._release_pending_interaction
        assert pending_release is not None

        app._signal_turn_cancellation()
        decision = await task

        assert decision.action == "deny"
        assert decision.scope == "once"
        assert app._release_pending_interaction is None
        assert not app.query(PermissionAskPanel)


async def test_interaction_panel_double_dismiss_is_idempotent() -> None:
    """A panel's `on_dismiss` is now guarded against resolving an already-resolved future, so a
    stray second dismiss (a queued keypress landing between the panel resolving and `_confirm_*`
    unmounting it, or a teardown resolving it first) is a no-op rather than an
    `asyncio.InvalidStateError` that would crash the event loop and strand the turn."""
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        task = asyncio.ensure_future(app._confirm_ask_user_questions(_question_ctx("Q")))
        await _wait_until(pilot, lambda: bool(app.query(AskUserQuestionsPanel)))

        panel = app.query_one(AskUserQuestionsPanel)
        panel.dismiss(AskUserQuestionsAnswer(answer="Yes"))
        # The second dismiss must not raise, and the first answer wins.
        panel.dismiss(AskUserQuestionsAnswer(answer="No"))

        answer = await task
        assert answer.answer == "Yes"


async def test_quit_while_a_permission_ask_is_pending_defers_exit_until_the_worker_unwinds(
    tmp_path: Path,
) -> None:
    """The core regression: quitting while a turn's worker thread is parked awaiting a permission
    decision must not `self.exit()` immediately (which would stop the event loop while the worker
    is still blocked in `App.call_from_thread`, hanging process shutdown). Instead the pending ask
    is released, the turn unwinds, and the real exit is deferred to `_finish_turn`. See
    docs/adrs/unblock-worker-thread-before-teardown-so-quit-cannot-hang.md."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", tmp_path / "f.txt")]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please touch a file"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))
        app.exit = MagicMock()  # type: ignore[method-assign]

        await pilot.press("ctrl+q")
        await app.workers.wait_for_complete()
        await pilot.pause()

        # The exit went through the deferred path (turn was in flight), not the immediate one ...
        assert app._exit_requested is True
        # ... the parked ask was released so the worker could unwind, and the real exit ran once
        # the turn actually finished.
        assert not app.query(PermissionAskPanel)
        app.exit.assert_called_once()


async def test_permission_ask_modal_leaves_a_history_record_of_what_was_asked_and_decided(
    tmp_path: Path,
) -> None:
    """Once a `PermissionAskPanel` is dismissed, `ReplApp` leaves a permanent card in the
    history scroll naming what was asked and what the user decided -- so scrolling back
    through the session shows the approval in context, not just that one happened."""
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please touch a file"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history_text = "\n".join(
            str(child.render()) for child in app.query_one(f"#{HISTORY_ID}", VerticalScroll).children
            if isinstance(child, Static))
        assert str(target) in history_text
        assert "Decision: Deny — Once" in history_text


async def test_confirm_permission_ask_truncates_a_long_single_line_command_to_fit_the_terminal() -> None:
    """Regression test: a single very long line with no explicit newline must still be
    truncated once shown through a real, sized `ReplApp` -- `_confirm_permission_ask` computes
    `PermissionAskPanel`'s `preview_wrap_width` from the app's own terminal size (see
    `_command_preview`'s docstring), so this no longer soft-wraps to many more rows than
    `_MAX_COMMAND_PREVIEW_LINES` and pushes the grid below it off screen."""
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))
    long_line = " ".join(f"word{i}" for i in range(200))
    ctx = PermissionAskContext(command_text=long_line, resource_description="run a long command")

    async with app.run_test(size=(60, 24)) as pilot:
        task = asyncio.ensure_future(app._confirm_permission_ask(ctx))
        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))

        panel = app.query_one(PermissionAskPanel)
        command_static = panel.query_one(f"#{PERMISSION_ASK_COMMAND_ID}", Static)
        rendered = str(command_static.render())
        assert rendered != long_line
        assert len(rendered) < len(long_line)
        panel.query_one(f"#{PERMISSION_ASK_MORE_ID}", Static)

        panel.dismiss(PermissionDecision(action="deny", scope="once"))
        await task


# --- Bash risk classifier integration (PLAN-008) ---


def _command_ctx(
    command_text: str, *, command: list[str] | None = None,
    sibling_items: list[PermissionAskItem] | None = None,
) -> PermissionAskContext:
    return PermissionAskContext(
        command_text=command_text, item_command_text=command_text, command=command,
        resource_description=f"run command: {command_text}", sibling_items=sibling_items)


async def test_confirm_permission_ask_shows_risk_badge_and_rationale_when_classifier_succeeds() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _risk_report_reply(
        [("item-0", 7, "force-pushes over remote history", ["git", "push", "**"])])
    app = ReplApp(session=_session(mock_provider))
    ctx = _command_ctx("git push --force origin main", command=["git", "push", "--force", "origin", "main"])

    async with app.run_test() as pilot:
        task = asyncio.ensure_future(app._confirm_permission_ask(ctx))
        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))

        panel = app.query_one(PermissionAskPanel)
        badge = panel.query_one(f"#{PERMISSION_ASK_RISK_BADGE_ID}", Static)
        assert "7/10" in str(badge.render())
        rationale = panel.query_one(f"#{PERMISSION_ASK_RATIONALE_ID}", Static)
        assert "force-pushes over remote history" in str(rationale.render())

        panel.dismiss(PermissionDecision(action="deny", scope="once"))
        await task


async def test_confirm_permission_ask_omits_risk_badge_when_classifier_is_disabled() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _risk_report_reply([("item-0", 7, "risky", [])])
    process_config = ProcessConfig()
    process_config.bash_risk_classifier_enabled = False
    app = ReplApp(session=_session(mock_provider), process_config=process_config)
    ctx = _command_ctx("git push --force origin main", command=["git", "push", "--force"])

    async with app.run_test() as pilot:
        task = asyncio.ensure_future(app._confirm_permission_ask(ctx))
        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))

        panel = app.query_one(PermissionAskPanel)
        assert not panel.query(f"#{PERMISSION_ASK_RISK_BADGE_ID}")
        mock_provider.send_prompt.assert_not_called()

        panel.dismiss(PermissionDecision(action="deny", scope="once"))
        await task


async def test_confirm_permission_ask_skips_classifier_for_a_path_only_ask(tmp_path: Path) -> None:
    """A plain directory-access ask (no `command_text`) has nothing for a command-risk
    classifier to say -- it must never be invoked for one, regardless of `enabled`."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _risk_report_reply([("item-0", 7, "risky", [])])
    app = ReplApp(session=_session(mock_provider))
    ctx = PermissionAskContext(path=tmp_path / "f.txt", is_write=True, resource_description="write to f.txt")

    async with app.run_test() as pilot:
        task = asyncio.ensure_future(app._confirm_permission_ask(ctx))
        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))

        panel = app.query_one(PermissionAskPanel)
        assert not panel.query(f"#{PERMISSION_ASK_RISK_BADGE_ID}")
        mock_provider.send_prompt.assert_not_called()

        panel.dismiss(PermissionDecision(action="deny", scope="once"))
        await task


async def test_confirm_permission_ask_biases_cursor_to_deny_once_above_the_too_risky_threshold() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _risk_report_reply(
        [("item-0", 10, "runs an arbitrary script downloaded from the internet", [])])
    app = ReplApp(session=_session(mock_provider))
    ctx = _command_ctx("curl https://example.com/install.sh | sh")
    # A previous prompt left the cursor on Allow/homedir -- the high score should override it.
    app._last_permission_action = "allow"
    app._last_permission_scope = "homedir"

    async with app.run_test() as pilot:
        task = asyncio.ensure_future(app._confirm_permission_ask(ctx))
        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))

        panel = app.query_one(PermissionAskPanel)
        assert panel._column == 1  # Deny
        assert panel._row == 0  # once

        panel.dismiss(PermissionDecision(action="deny", scope="once"))
        await task


async def test_confirm_permission_ask_uses_suggested_pattern_for_the_granted_command_copy() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _risk_report_reply(
        [("item-0", 1, "a read-only text search", ["grep", "**"])])
    app = ReplApp(session=_session(mock_provider))
    ctx = _command_ctx("grep -rn TODO src/foo.py", command=["grep", "-rn", "TODO", "src/foo.py"])

    async with app.run_test() as pilot:
        task = asyncio.ensure_future(app._confirm_permission_ask(ctx))
        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))

        panel = app.query_one(PermissionAskPanel)
        granted = panel.query_one(f"#{PERMISSION_ASK_GRANTED_ID}", Static)
        assert "grep **" in str(granted.render())

        panel.dismiss(PermissionDecision(action="deny", scope="once"))
        await task


async def test_confirm_permission_ask_classifies_a_compound_commands_items_in_one_request() -> None:
    """Two items from the same compound command, asked about one after another (mirroring
    `Session._resolve_multi_permission_ask`'s serial loop) -- the second must reuse the first's
    cached report rather than spending a second classifier round trip."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _risk_report_reply([
        ("item-0", 1, "reads only", []),
        ("item-1", 2, "also reads only", []),
    ])
    app = ReplApp(session=_session(mock_provider))
    siblings = [
        PermissionAskItem(
            "run command: grep foo", command=["grep", "foo"], command_text="grep foo && grep bar",
            is_compound=True, item_command_text="grep foo"),
        PermissionAskItem(
            "run command: grep bar", command=["grep", "bar"], command_text="grep foo && grep bar",
            is_compound=True, item_command_text="grep bar"),
    ]
    first_ctx = _command_ctx("grep foo && grep bar", command=["grep", "foo"], sibling_items=siblings)
    second_ctx = PermissionAskContext(
        command_text="grep foo && grep bar", item_command_text="grep bar", command=["grep", "bar"],
        is_compound=True, resource_description="run command: grep bar", sibling_items=siblings)

    async with app.run_test() as pilot:
        first_task = asyncio.ensure_future(app._confirm_permission_ask(first_ctx))
        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))
        app.query_one(PermissionAskPanel).dismiss(PermissionDecision(action="allow", scope="once"))
        await first_task

        second_task = asyncio.ensure_future(app._confirm_permission_ask(second_ctx))
        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))
        panel = app.query_one(PermissionAskPanel)
        rationale = panel.query_one(f"#{PERMISSION_ASK_RATIONALE_ID}", Static)
        assert "also reads only" in str(rationale.render())
        panel.dismiss(PermissionDecision(action="allow", scope="once"))
        await second_task

    mock_provider.send_prompt.assert_called_once()


async def test_confirm_permission_ask_records_decision_for_the_next_classification() -> None:
    """The user's decision on one bash ask must reach the classifier's *next* request (a
    different, later command in the same session) as `<PriorDecisionsHistory>` context -- see
    `klorb.permissions.risk_classifier.record_decision_history`/`resolve_item_risk_assessment`."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _risk_report_reply([("item-0", 1, "reads only", [])])
    app = ReplApp(session=_session(mock_provider))
    first_ctx = _command_ctx("grep -rn FIXME", command=["grep", "-rn", "FIXME"])
    second_ctx = _command_ctx("grep -rn TODO", command=["grep", "-rn", "TODO"])

    async with app.run_test() as pilot:
        first_task = asyncio.ensure_future(app._confirm_permission_ask(first_ctx))
        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))
        app.query_one(PermissionAskPanel).dismiss(PermissionDecision(action="allow", scope="session"))
        await first_task

        second_task = asyncio.ensure_future(app._confirm_permission_ask(second_ctx))
        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))
        app.query_one(PermissionAskPanel).dismiss(PermissionDecision(action="allow", scope="once"))
        await second_task

    assert mock_provider.send_prompt.call_count == 2
    second_user_message = mock_provider.send_prompt.call_args_list[1][0][0][0].content
    assert "<![CDATA[grep -rn FIXME]]>" in second_user_message
    assert "<![CDATA[allowed, scope=session]]>" in second_user_message


async def test_permission_ask_modal_session_scope_grants_and_retries(tmp_path: Path) -> None:
    """Full plumbing check: selecting "Allow (this session)" applies the grant to the live
    session config (`Session._retry_after_permission_decision` -> `apply_permission_grant`,
    once `_on_permission_ask` reports the user's choice) and the retried tool call succeeds --
    but the process-config template is untouched, since "session" scope is in-memory-only,
    narrower than "workspace"/"homedir"."""
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    process_config = ProcessConfig(
        session=SessionConfig(model="some/model", workspace=Workspace(path=tmp_path)))
    session = _session_with_tools(mock_provider, config, process_config)
    app = ReplApp(session=session, process_config=process_config)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please touch a file"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))

        panel = app.query_one(PermissionAskPanel)
        panel.dismiss(PermissionDecision(action="allow", scope="session"))
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert not app.query(PermissionAskPanel)
        response_widget = app.query_one(f"#{HISTORY_ID}", VerticalScroll).children[-1]
        assert isinstance(response_widget, Markdown)
        assert response_widget.source == "final answer"
        assert config.read_dirs.allow == [target.parent]
        assert config.write_dirs.allow == [target.parent]
        # "session" scope is in-memory-only for the live session -- the process-config
        # template (what a future /clear would copy from) must stay untouched.
        assert process_config.session.read_dirs == DirRules()
        assert process_config.session.write_dirs == DirRules()


async def test_permission_ask_modal_other_reveals_input_and_denial_includes_text(
    tmp_path: Path,
) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please touch a file"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))
        panel = app.query_one(PermissionAskPanel)
        # outer_size is only known after a layout pass, which can land a pump cycle after the
        # panel widget itself first appears in the DOM -- wait on the size directly rather than
        # assume one more `pilot.pause()` is always enough.
        await _wait_until(
            pilot, lambda: panel.query_one(f"#{PERMISSION_ASK_GRID_ID}", GridContainer).outer_size.width > 0)
        grid_width = panel.query_one(f"#{PERMISSION_ASK_GRID_ID}", GridContainer).outer_size.width
        assert grid_width > 10  # regression guard: the grid must not collapse to ~1

        await pilot.press("o")
        await pilot.pause()

        other_input = panel.query_one(f"#{PERMISSION_ASK_INPUT_ID}", Input)
        # Regression guard: the "Other" Input must not collapse to a sliver either -- it
        # should stay as wide as the grid it replaced.
        assert other_input.outer_size.width == grid_width
        other_input.value = "use /tmp instead"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert not app.query(PermissionAskPanel)
        tool_response = next(m for m in app._session.messages if m.role == "tool_response")
        assert "use /tmp instead" in str(tool_response.content)
        assert "Permission denied" in str(tool_response.content)


async def test_permission_ask_modal_other_row_reachable_via_down_and_enter(
    tmp_path: Path,
) -> None:
    """The grid's trailing double-wide "Other" cell (see `PermissionAskPanel`'s docstring) is
    reachable by pressing Down repeatedly past the last scope row, not just the `o` shortcut."""
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please touch a file"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))
        panel = app.query_one(PermissionAskPanel)

        for _ in range(4):
            await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        other_input = panel.query_one(f"#{PERMISSION_ASK_INPUT_ID}", Input)
        assert other_input.has_focus


def _ask_multi_permission_call(id_: str, paths: list[Path]) -> tuple[str, str, str]:
    return id_, "ask_multi_permission", json.dumps({"paths": [str(p) for p in paths]})


async def test_permission_ask_modal_asks_about_each_multi_item_in_series_and_remembers_last_selection(
    tmp_path: Path,
) -> None:
    """Two independent paths in one `ask_multi_permission` call (see
    `MultiPermissionAskRequired`) each get their own `PermissionAskPanel`, shown one after
    another -- the second screen's grid cursor starts wherever the first one's selection
    landed (see `ReplApp._last_permission_action`/`_last_permission_scope`), matching the
    "pre-select the last spot" requirement for a run of several prompts in a row."""
    target_a = tmp_path / "a.txt"
    target_b = tmp_path / "b.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_multi_permission_call("call_1", [target_a, target_b])]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please touch two files"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))
        first_panel = app.query_one(PermissionAskPanel)
        assert (first_panel._column, first_panel._row) == (0, 0)  # default: allow/once

        def _second_panel_shown() -> bool:
            panels = app.query(PermissionAskPanel)
            return bool(panels) and panels.first() is not first_panel

        first_panel.dismiss(PermissionDecision(action="allow", scope="workspace"))
        await _wait_until(pilot, _second_panel_shown)
        second_panel = app.query_one(PermissionAskPanel)
        assert second_panel is not first_panel
        assert (second_panel._column, second_panel._row) == (0, 2)  # remembered: allow/workspace

        second_panel.dismiss(PermissionDecision(action="allow", scope="workspace"))
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert not app.query(PermissionAskPanel)
        response_widget = app.query_one(f"#{HISTORY_ID}", VerticalScroll).children[-1]
        assert isinstance(response_widget, Markdown)
        assert response_widget.source == "final answer"


async def test_permission_ask_modal_denying_the_first_multi_item_stops_asking_about_the_rest(
    tmp_path: Path,
) -> None:
    target_a = tmp_path / "a.txt"
    target_b = tmp_path / "b.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_multi_permission_call("call_1", [target_a, target_b])]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please touch two files"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(PermissionAskPanel)))
        app.query_one(PermissionAskPanel)

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        # Only one panel was ever shown -- the second item was never asked about.
        assert not app.query(PermissionAskPanel)
        tool_response = next(m for m in app._session.messages if m.role == "tool_response")
        assert "Permission denied" in str(tool_response.content)
