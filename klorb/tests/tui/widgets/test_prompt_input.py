# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.widgets.prompt_input.PromptInput: input history recall,
reverse-incremental search, and the inline command palette."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from textual import events
from textual.containers import VerticalScroll
from textual.widgets import Static
from tui.conftest import (
    TEST_SESSION_ID,
    _complete_turn,
    _invoke_clear_session,
    _isolated_data_dir,
    _palette_hit_texts,
    _process_config_for_workspace,
    _reply,
    _session,
    _wait_until,
)

from klorb.process_config import ProcessConfig
from klorb.session import Session
from klorb.tui.app import ReplApp
from klorb.tui.constants import HISTORY_ID, PROMPT_INPUT_ID
from klorb.tui.widgets.palette import PALETTE_PREFIX, PROMPT_PALETTE_ID, PaletteOption, PromptPalette
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.workspace import TrustManager, Workspace
from klorb.workspace.input_history import project_history_path


async def test_prompt_input_max_height_comes_from_process_config() -> None:
    mock_provider = MagicMock()
    process_config = ProcessConfig(prompt_input_max_lines=3)
    app = ReplApp(session=_session(mock_provider), process_config=process_config)

    async with app.run_test():
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        max_height = prompt_input.styles.max_height
        assert max_height is not None
        assert int(max_height.value) == 4


async def test_up_arrow_recalls_the_most_recent_prompt() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        assert prompt_input.text == ""
        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "first"


async def test_repeated_up_arrow_walks_back_through_history() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = "second"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "second"

        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "first"


async def test_paste_inserts_text_exactly_once() -> None:
    """A single bracketed-paste event inserts the pasted text once, not twice. `PromptInput`
    overrides `_on_paste` to detach from history before the box mutates; because Textual
    dispatches an event to every `_on_paste` along the widget's MRO, that override must not
    also call `super()._on_paste` or the base insertion runs twice (the Ctrl+V-pastes-twice
    bug). The event is delivered via `app.post_message` exactly as the input driver forwards a
    real bracketed paste to the focused widget."""
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.focus()
        await pilot.pause()

        app.post_message(events.Paste("pasted text"))
        await _wait_until(pilot, lambda: prompt_input.text != "")
        assert prompt_input.text == "pasted text"


async def test_paste_after_recalling_history_starts_a_fresh_draft() -> None:
    """Pasting detaches from history even though `_on_paste` no longer calls `super()` -- the
    detach happens in the override itself, and the base handler that actually inserts the text
    runs afterward via Textual's MRO dispatch. So a paste on top of a recalled entry appends to
    it as an ordinary edit rather than being discarded as part of the read-only recall."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "recalled"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "recalled"
        assert prompt_input._history_index is not None

        app.post_message(events.Paste(" edited"))
        await _wait_until(pilot, lambda: prompt_input.text != "recalled")
        assert prompt_input.text == "recalled edited"
        assert prompt_input._history_index is None


async def test_down_arrow_walks_forward_and_returns_to_empty_draft() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = "second"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "second"

        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "first"

        await pilot.press("down")
        await pilot.pause()
        assert prompt_input.text == "second"

        await pilot.press("down")
        await pilot.pause()
        assert prompt_input.text == ""


async def test_down_arrow_past_most_recent_restores_the_in_progress_draft() -> None:
    """Walking up from an untouched draft and back down past the most recent entry restores
    that draft rather than clearing the box to empty."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = "an unsent draft"
        await pilot.press("home")
        await pilot.pause()

        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "first"

        await pilot.press("down")
        await pilot.pause()
        assert prompt_input.text == "an unsent draft"


async def test_editing_a_recalled_prompt_detaches_and_resets_recall_position() -> None:
    """After typing into a recalled entry, the next up-arrow starts fresh from the most
    recent entry rather than continuing from the now-stale recall position."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = "second"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        # Walk back to the oldest entry, then edit it.
        await pilot.press("up", "up")
        await pilot.pause()
        assert prompt_input.text == "first"
        await pilot.press("x")
        await pilot.pause()
        assert prompt_input.text == "firstx"

        # Move to the start so the next up-arrow triggers recall rather than cursor movement.
        await pilot.press("home")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        # Detached, so recall jumps to the most recent entry ("second"), not continuing from
        # the stale position that would have gone past "first" with nowhere to go.
        assert prompt_input.text == "second"


async def test_empty_prompt_is_not_recorded_in_history() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        # An empty submit doesn't get recorded, so there's still only one history entry.
        prompt_input.text = "   "
        await pilot.press("enter")
        await pilot.pause()
        assert prompt_input.text == "   "

        await pilot.press("home", "up")
        await pilot.pause()
        assert prompt_input.text == "first"


async def test_clear_resets_input_history() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        await _invoke_clear_session(pilot)

        # Clearing the session drops "first" from the input history, but the
        # ">Clear session" selection that triggered it is recorded afterward (see
        # `ReplApp._run_palette_command`), so it's the sole entry left to recall — not
        # "first", and no entry further back than it either.
        assert prompt_input.text == ""
        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == f"{PALETTE_PREFIX}Clear session"
        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == f"{PALETTE_PREFIX}Clear session"


# --- file-backed input history (persistence across sessions) ---


def _repl_app_with_mock_provider(
    workspace: Workspace, trust_manager: TrustManager, model: str = "some/model",
) -> tuple[ReplApp, MagicMock]:
    """Build a `ReplApp` whose `Session` uses a `MagicMock` provider the caller keeps a
    typed reference to, so configuring `mock.send_prompt.return_value` satisfies mypy
    (`Session.provider` is typed as a real `ApiProvider`, so reaching for `.return_value`
    through it is an `attr-defined` error — see `_session` for the same pattern)."""
    mock_provider = MagicMock()
    process_config = _process_config_for_workspace(workspace, model)
    session = Session(
        process_config.session.model_copy(), provider=mock_provider, session_id=TEST_SESSION_ID,
        process_config=process_config)
    return ReplApp(session=session, process_config=process_config, trust_manager=trust_manager), mock_provider


async def test_submitted_prompt_persists_to_history_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prompt submitted in one session is appended to the on-disk `history` file under the
    project's per-project directory."""
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    app, mock_provider = _repl_app_with_mock_provider(workspace, trust_manager)
    mock_provider.send_prompt.return_value = _reply()

    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "persisted prompt"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

    history_file = project_history_path(app._session.config.workspace)
    assert history_file.is_file()
    assert history_file.read_text(encoding="utf-8") == "persisted prompt\n"


async def test_history_seeds_from_file_on_startup_so_prior_sessions_are_recallable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prompt submitted in an earlier session is recallable via up-arrow in a fresh session
    opened in the same project — the in-memory history is seeded from the on-disk file at
    startup (see `PromptInput.set_history_store`)."""
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)

    # First session: submit a prompt so it lands on disk.
    app, mock_provider = _repl_app_with_mock_provider(workspace, trust_manager)
    mock_provider.send_prompt.return_value = _reply()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "older prompt"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

    history_file = project_history_path(workspace)
    assert history_file.is_file()

    # Second session: a fresh ReplApp in the same (registered) project seeds from the file.
    app2, _ = _repl_app_with_mock_provider(workspace, trust_manager)
    async with app2.run_test() as pilot:
        await pilot.pause()
        await app2.workers.wait_for_complete()
        await pilot.pause()
        prompt_input2 = app2.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        # No new submission yet — up-arrow should recall the prompt from the *first* session.
        await pilot.press("up")
        await pilot.pause()
        assert prompt_input2.text == "older prompt"


# --- Ctrl+R reverse-incremental-search ---


async def test_ctrl_r_finds_the_most_recent_matching_prompt() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hello world"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = "goodbye world"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = ""
        await pilot.pause()
        # Ctrl+R then type "hello" — should find "hello world" (the only entry containing it).
        await pilot.press("ctrl+r")
        await pilot.pause()
        for ch in "hello":
            await pilot.press(ch)
        await pilot.pause()
        assert prompt_input.text == "hello world"


async def test_ctrl_r_search_is_case_insensitive() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "Fix the Bug"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = ""
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()
        for ch in "bug":
            await pilot.press(ch)
        await pilot.pause()
        assert prompt_input.text == "Fix the Bug"


async def test_repeated_ctrl_r_advances_to_older_match() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "search me alpha"
        await pilot.press("enter")
        await _complete_turn(pilot, app)
        prompt_input.text = "search me beta"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = ""
        await pilot.pause()
        # Type the common substring, then press Ctrl+R again for the next-older match.
        await pilot.press("ctrl+r")
        await pilot.pause()
        for ch in "search me":
            await pilot.press(ch)
        await pilot.pause()
        assert prompt_input.text == "search me beta"
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert prompt_input.text == "search me alpha"


async def test_ctrl_r_enter_exits_search_and_submits() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "find me"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = ""
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()
        for ch in "find":
            await pilot.press(ch)
        await pilot.pause()
        assert prompt_input.text == "find me"
        # Enter exits the search and submits the recalled match.
        await pilot.press("enter")
        await pilot.pause()
        assert prompt_input.text == ""
        assert prompt_input._isearch_active is False


async def test_ctrl_r_escape_exits_search_without_submitting() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "find me"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = ""
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()
        for ch in "find":
            await pilot.press(ch)
        await pilot.pause()
        assert prompt_input.text == "find me"
        # Escape exits the search, leaving the match in the box (not submitted).
        await pilot.press("escape")
        await pilot.pause()
        assert prompt_input.text == "find me"
        assert prompt_input._isearch_active is False


# --- command palette from the prompt (a leading ">" in the prompt input) ---


async def test_bare_gt_shows_the_full_discovery_list() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(">")
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert palette.display is True
        assert palette.option_count > 0
        assert "Clear session" in _palette_hit_texts(palette)


async def test_bare_gt_lists_rows_alphabetically_since_every_score_ties_at_zero() -> None:
    """A `DiscoveryHit` (what `discover()` yields for the empty query behind a bare `>`)
    always scores `0.0`, so with nothing to rank by, the alphabetical tiebreak alone decides
    the full listing's order (see `gather_palette_hits`).
    """
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(">")
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        options = palette._options
        assert all(isinstance(option, PaletteOption) for option in options)
        texts = [str(option.hit.text) for option in options if isinstance(option, PaletteOption)]
        assert texts == sorted(texts, key=str.casefold)


async def test_typing_a_query_narrows_the_palette_to_matching_hits() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(*f"{PALETTE_PREFIX}clear")
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert palette.display is True
        assert _palette_hit_texts(palette) == {"Clear session"}


async def test_up_down_arrows_move_the_palette_highlight_not_the_cursor() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(">")
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        first_highlight = palette.highlighted
        assert first_highlight is not None

        await pilot.press("down")
        await pilot.pause()
        assert palette.highlighted == first_highlight + 1

        await pilot.press("up")
        await pilot.pause()
        assert palette.highlighted == first_highlight

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        assert prompt_input.text == ">"


async def test_enter_executes_the_highlighted_palette_command_and_clears_the_input() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(*f"{PALETTE_PREFIX}clear")
        await pilot.press("enter")
        await pilot.pause()

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert prompt_input.text == ""
        assert palette.display is False
        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        # clear_session() ran (not a submitted prompt), leaving only its own "Session cleared."
        # notice behind.
        assert len(history.children) == 1


async def test_palette_selection_is_recorded_in_history_by_its_canonical_name() -> None:
    """Recalling a palette selection via up-arrow should show `>Clear session` (the
    canonical/standard name), not whatever partial query the user actually typed.
    """
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(*f"{PALETTE_PREFIX}cle")
        await pilot.press("enter")
        await pilot.pause()

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == f"{PALETTE_PREFIX}Clear session"


async def test_recalling_a_past_palette_selection_does_not_resurface_the_popup() -> None:
    """Browsing history up to a recalled `>Clear session` entry (per
    docs/specs/command-palette-from-prompt.md's "History browsing" section) shows it as
    plain recalled text; the popup only reappears once the user actually edits it.
    """
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(*f"{PALETTE_PREFIX}clear")
        await pilot.press("enter")
        await pilot.pause()

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        await pilot.press("up")
        await pilot.pause()

        assert prompt_input.text == f"{PALETTE_PREFIX}Clear session"
        assert palette.display is False


async def test_escape_dismisses_the_palette_and_continues_as_plain_text() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(*f"{PALETTE_PREFIX}clear")
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert palette.display is True

        await pilot.press("escape")
        await pilot.pause()
        assert palette.display is False

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)  # type: ignore[unreachable]
        await pilot.press("!")
        await pilot.pause()
        assert prompt_input.text == f"{PALETTE_PREFIX}clear!"
        assert palette.display is False


async def test_enter_with_no_matching_palette_option_submits_as_a_plain_prompt() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        # Set directly rather than typed key-by-key: only the end state (a non-matching
        # draft) matters here, and `.text =` still fires the same `TextArea.Changed` ->
        # `_refresh_palette` path a real keystroke would, without paying `Pilot.press`'s real
        # ~20-40ms-per-key `wait_for_idle` cost.
        gibberish = f"{PALETTE_PREFIX}pxv"
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = gibberish
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert palette.display is False

        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompt_widget = history.query(Static).exclude(".mascot").first()
        assert prompt_widget.content == gibberish


async def test_no_match_then_space_dismisses_the_palette() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        # Set directly rather than typed key-by-key (see
        # test_enter_with_no_matching_palette_option_submits_as_a_plain_prompt for why); only
        # the "space" keystroke below needs to be a real key, since it's the one that latches
        # `_palette_dismissed`.
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = f"{PALETTE_PREFIX}pxv"
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert palette.display is False

        await pilot.press("space")
        await pilot.pause()
        prompt_input.text = f"{PALETTE_PREFIX}pxv ok"
        await pilot.pause()

        assert prompt_input.text == f"{PALETTE_PREFIX}pxv ok"
        assert palette.display is False


async def test_backspacing_the_leading_gt_to_empty_closes_the_palette() -> None:
    """Regression test: backspace is dispatched via a bound `TextArea` action
    (`action_delete_left`) that Textual applies *after* `_on_key` returns, not from within
    it — so `_refresh_palette` can't reliably read the post-edit text if called directly from
    `_on_key`. It's driven from `on_text_area_changed` instead (see that method's docstring),
    since `TextArea.Changed` only fires once the edit has actually landed.
    """
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        await pilot.press(">")
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert palette.display is True

        await pilot.press("backspace")
        await pilot.pause()

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        assert prompt_input.text == ""
        assert palette.display is False
        assert palette.option_count == 0  # type: ignore[unreachable]

        # Up/down should now drive ordinary history recall again, not a (closed) popup.
        prompt_input.text = "earlier prompt"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press(">")
        await pilot.press("backspace")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "earlier prompt"
