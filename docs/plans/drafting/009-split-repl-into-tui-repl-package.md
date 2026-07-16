# Split `klorb/tui/repl.py` into cohesive modules under `klorb/tui/`

Claude: this plan is a **draft**, not ready for implementation. It lays out a target module
layout and the *mechanics* of how to move the code (so the move is done with cut/paste
tooling, not retyped from context). Do not implement any part of this until it's moved to
`ready/`.

## Context

`klorb/src/klorb/tui/repl.py` is 3,456 lines and `klorb/tests/test_tui_repl.py` is 4,786
lines (199 test functions). Both are the largest files in their respective trees by a wide
margin, and both keep growing because every new REPL behavior has nowhere else to go. The
rest of `klorb/src/klorb/tui/` is 12 more files (2,470 lines) that exist only to be consumed
by `repl.py` and its tests:

```
klorb/src/klorb/tui/
    __init__.py                    (2 lines)
    ask_user_questions_panel.py    (229)
    confirm_screen.py              (167)
    escalate_privileges_panel.py   (163)
    init_commands.py               (52)
    model_commands.py              (200)
    model_info_commands.py         (134)
    palette.py                     (137)
    permission_ask_panel.py        (618)
    repl.py                        (3,456)
    session_commands.py            (61)
    shell.py                       (128)
    theme_commands.py              (117)
    thinking_commands.py           (132)
    trust_commands.py              (59)
```

Checked (`grep`, see below): outside of `klorb/tui/` itself and its tests, exactly one thing
imports from this package — `klorb/src/klorb/cli.py` does `from klorb.tui.repl import
run_repl`. Nothing else in `klorb/` reaches into `klorb.tui.*` directly; every other mention
of `klorb.tui.repl.ReplApp` across the codebase (dozens, in ADRs and docstrings in
`session.py`, `process_config.py`, `watchdog.py`, `risk_classifier.py`, etc.) is prose, not an
import.

That fact settles a question this plan originally left open: whether the split should live
under a new `klorb/tui/repl/` subpackage, or be rooted directly in `klorb/tui/`. `klorb.tui`
has no meaning today beyond "the REPL" — there is no other Textual UI mode in this codebase
for a `repl/` subpackage to be distinguished from. Adding that extra namespace segment would
buy vocabulary, not clarity, for a distinction that doesn't exist yet. This plan roots
everything directly under `klorb/tui/` instead. If a second, genuinely different TUI screen
ever gets built, `klorb/tui/{app.py,mixins/,widgets/,panels/,commands/,...}` can be extracted
into `klorb/tui/repl/` at that point — a small, well-contained move — rather than carrying the
extra nesting now for a consumer that doesn't exist (see CLAUDE.md's guidance against
designing for hypothetical future requirements).

## Goals

* Nothing in `klorb/tui/` should be an "enormous" file by this codebase's existing norms
  (most source files here are under a few hundred lines; `permission_ask_panel.py` at 618
  lines is the biggest thing that *isn't* `repl.py` today).
* One source file, one test file (`docs/plans/README-PLANS.md` conventions still apply: no
  behavior change, this is pure restructuring). Every panel gets an isolated, `ReplApp`-free
  unit-test file, matching the pattern `AskUserQuestionsPanel` already has and
  `EscalatePrivilegesPanel` currently lacks (see "Test layout" below).
* `klorb.tui.run_repl` keeps working unchanged for `cli.py` (import path moves from
  `klorb.tui.repl.run_repl` to `klorb.tui.run_repl` — a one-line, mechanical update to
  `cli.py`, not a public-behavior change).
* The move is mechanical: line ranges get cut from the old files into new ones with
  `sed`/sentinel-based extraction, not retyped by an agent reading and reproducing the code.
  Retyping ~8,300 lines of Textual/asyncio code from an LLM's own context is exactly how
  subtle bugs (a dropped `await`, a renamed local, a reordered CSS rule) get introduced
  without anyone noticing.

## Non-goals

* No behavior change. This plan does not touch what the REPL does, only where its code lives.
* No change to `klorb/tui/`'s real external touch point: the (relocated) `run_repl` `cli.py`
  imports, and the CSS/keybindings the user sees.
* Not attempting to also flatten/reorganize the 74 other flat files under `klorb/tests/` —
  only the tests that map onto the modules touched here move into a nested layout, per the
  task's explicit "tests can have a dir structure that matches the source they test, they
  don't need to be flat" allowance. The rest of `klorb/tests/` stays exactly as it is.

## Current shape of `repl.py`

`repl.py` is, top to bottom:

1. Module docstring + ~90 lines of imports (lines 1–95).
2. Module-level helper functions and constants, interleaved rather than grouped (lines
   96–303): `_concat_dir_rules`, `_random_greeting`, `_round_to_2_sig_figs`,
   `format_token_count`, `_summarize_reasoning_details`, `_format_workspace_path`,
   `_pinned_to_bottom`, plus widget/DOM-id constants (`HISTORY_ID`, `PROMPT_INPUT_ID`, ...),
   label strings (`THINKING_LABEL`, ...), and magic numbers. More constants of the same kind
   reappear later in the file, right before the widget that uses them (e.g.
   `_ANIMATED_RUNNING_TEXT` at line 940, next to `RunningToolCallStatic`;
   `PERMISSION_BADGE_WIDTH` at line 142, next to `PermissionBadge`).
3. Five self-contained widget/screen classes, in order (lines 303–1222):
   `PromptInput` (303–857, 554 lines — the inline command palette wiring, isearch, and
   history recall all live on this one class), `ToolCallLimitScreen` (857–921),
   `ToolCallStatic`/`RunningToolCallStatic` (921–1022), `PaletteHint` (1022–1102),
   `PermissionBadge` (1102–1181), `SelectionSafeScreen` (1181–1222).
4. `ReplApp(App[None])` itself (lines 1222–3383, ~2,160 lines — 63% of the file). One class,
   ~150 methods. Reading the method list top to bottom, they cluster cleanly into eight
   groups (approximate line ranges below; re-derive exact boundaries with `grep -n` at
   implementation time since numbers will have shifted):
   * **Core/lifecycle** (1222–1635, ~410 lines): `CSS`, `BINDINGS`, `COMMANDS`, `__init__`,
     `compose`, `get_default_screen`, model/theme selection, `format_title`, thinking-effort
     getters/setters.
   * **Key handling, actions, quit/exit, watchdog** (1635–2028, ~390 lines): `on_key`,
     `check_action`, `action_abort_response`, `action_interrupt`, `action_quit` and its
     `@work` continuation, `_begin_exit`, `_release_workers_for_exit`,
     `action_toggle_tool_call_detail`, `on_mount`/`on_unmount`, watchdog snoozing/hang
     diagnostics, `_force_exit`.
   * **Workspace bootstrap & trust** (2028–2251, ~220 lines):
     `_run_startup_workspace_and_initial_message`, `_resolve_workspace_trust`,
     `_maybe_restore_last_session`, `_bootstrap_new_workspace`, `_apply_workspace_config`,
     `_announce_workspace`, `trust_workspace`.
   * **Status bar / palette hint / permission badge** (2251–2338, ~90 lines):
     `_update_palette_hint`, `_update_status_bar`, `_update_permission_badge`,
     `_cycle_permission_framework`.
   * **Prompt submission & shell commands** (2338–2727, ~390 lines):
     `on_prompt_input_submitted`, `_submit_shell_command`, `_run_shell_command`,
     `clear_session`, `_submit_prompt`, `_send_prompt` (the `@work(thread=True)` turn-driving
     method — the single biggest method in the file).
   * **Response / tool-call rendering & mounting** (2727–3012, ~285 lines):
     `_mount_response_widget`, `_mount_thinking_widget`, `_render_tool_call`,
     `_mount_tool_call_widget`, `_mount_running_tool_call_widget`, `_mount_restored_history`,
     `_render_restored_tool_call`.
   * **Interaction flows** (3012–3288, ~280 lines): the permission-ask, ask-user-questions,
     and escalate-privileges confirm/on-event method pairs, plus
     `_enter_interaction_mode`/`_exit_interaction_mode`/`_record_interaction_history`.
   * **Turn finalization** (3288–3383, ~95 lines): `_finalize_streamed_response`,
     `_show_response`, `_show_error`, `_handle_aborted_response`, `_finish_turn`.
5. Module tail (3383–3456, ~75 lines): `_handle_repl_crash`, `run_repl` — the `cli.py`-facing
   entry point.

`ReplApp` being one 2,160-line class is the real obstacle: a Textual `App` subclass can't be
split across files by simply moving contiguous chunks the way the widget classes above can,
because there's only one class. Two ways to actually break it up:

* **Composition** (each concern becomes a helper object `ReplApp` holds a reference to, e.g.
  `self._prompt_controller = PromptController(self)`) is the more conventional OO answer, but
  it requires rewriting every call site (`self._mount_tool_call_widget(...)` becomes
  `self._rendering.mount_tool_call_widget(...)` at every one of its call sites, of which there
  are many, scattered across the groups above). That's exactly the kind of manual, no
  cut-and-paste-tool edit this plan is supposed to avoid — every call site is a chance to
  typo a rename.
* **Mixins** (each group becomes a plain class holding a slice of the methods, unchanged
  internally, and `ReplApp` is assembled as
  `class ReplApp(KeyActionsMixin, WorkspaceBootstrapMixin, ..., ReplAppBase):`) let the method
  bodies move verbatim — `self.foo(...)` still resolves at runtime via MRO regardless of
  which mixin `foo` physically lives in, so there is no call-site rewriting at all. **This is
  the approach this plan uses** — confirmed, not left open, despite it being a pattern new to
  this codebase.

The cost of mixins is typing: this repo's `make typecheck` runs mypy with
`--disallow-untyped-calls`, `--disallow-untyped-globals`, and `--disallow-redefinition`, so a
mixin method referencing `self.some_attr_set_by___init__` needs `some_attr` to be visible on
`self`'s declared type from that file's point of view — mypy doesn't know that whichever
concrete class eventually mixes this class in will also mix in the one that sets it. The fix
is a shared, attribute-only typed base class, `_base.py` below.

## Target source layout

```
klorb/src/klorb/tui/
    __init__.py             # public surface only: re-exports ReplApp, run_repl
    constants.py            # DOM/widget ids, label strings, magic numbers shared by 2+ modules
    formatting.py           # pure helper functions: format_token_count,
                             #   _summarize_reasoning_details, _concat_dir_rules,
                             #   _random_greeting, _round_to_2_sig_figs,
                             #   _format_workspace_path, _pinned_to_bottom
    _base.py                # ReplAppBase(App[None]): attribute-only typed base every mixin
                             #   and ReplApp itself inherits, so mypy can see cross-mixin
                             #   `self.` access. No behavior, just declarations.
    app.py                  # ReplApp: CSS/BINDINGS/COMMANDS, __init__, compose, and the
                             #   "core/lifecycle" method group from above.
                             #   SelectionSafeScreen also lives here (see below).
    entrypoint.py           # _handle_repl_crash, run_repl
    mixins/
        __init__.py
        key_actions.py          # key handling / actions / quit / watchdog group
        workspace_bootstrap.py  # workspace bootstrap & trust group
        status_bar.py           # status bar / palette hint / permission badge group
        prompt_submission.py    # prompt submission / shell commands / turn-finalization
                                 #   groups (kept together: _send_prompt's @work body and
                                 #   _finish_turn/_show_response/etc. are one control flow)
        rendering.py             # response / tool-call rendering & mounting group
        interactions.py          # permission-ask / ask-user-questions / escalate-privileges
                                 #   confirm flows
    widgets/
        __init__.py
        prompt_input.py          # PromptInput (moved verbatim)
        tool_call_widgets.py     # ToolCallLimitScreen, ToolCallStatic, RunningToolCallStatic
        status_widgets.py        # PaletteHint, PermissionBadge
        palette.py                # moved from klorb/tui/palette.py
    panels/
        __init__.py
        ask_user_questions_panel.py    # moved from klorb/tui/, unchanged
        confirm_screen.py               # moved from klorb/tui/, unchanged
        escalate_privileges_panel.py    # moved from klorb/tui/, unchanged
        permission_ask_panel.py         # moved from klorb/tui/, unchanged
    commands/
        __init__.py
        init_commands.py         # moved from klorb/tui/
        model_commands.py        # moved from klorb/tui/
        model_info_commands.py   # moved from klorb/tui/
        session_commands.py      # moved from klorb/tui/
        theme_commands.py        # moved from klorb/tui/
        thinking_commands.py     # moved from klorb/tui/
        trust_commands.py        # moved from klorb/tui/
    shell.py                     # already at this path today; no move needed
```

Confirmed groupings (not left open): `panels/` and `commands/` are real, intentional
subpackages, not a flat dump.

* `ask_user_questions_panel.py`, `confirm_screen.py`, `escalate_privileges_panel.py`, and
  `permission_ask_panel.py` are all "modal-ish interactive confirmation UI mounted into
  `#interaction-panel`" — the same conceptual group `docs/specs/terminal-repl.md`'s
  "Interaction panel" section already describes as one thing.
* `init_commands.py`, `model_commands.py`, `model_info_commands.py`, `session_commands.py`,
  `theme_commands.py`, `thinking_commands.py`, and `trust_commands.py` are all
  `SystemCommandProvider` subclasses feeding `ReplApp.COMMANDS` (Textual's built-in command
  palette) — same shape, same consumer, natural to browse together.

### Public API surface (`__init__.py`)

Only `ReplApp` and `run_repl` are re-exported from `klorb/tui/__init__.py`. `run_repl` is the
real external contract (`cli.py` imports it: update `cli.py`'s
`from klorb.tui.repl import run_repl` to `from klorb.tui import run_repl`). `ReplApp` isn't
imported anywhere outside `klorb/tui/` and its tests today, but dozens of docstrings elsewhere
in the codebase refer to `klorb.tui.repl.ReplApp.<method>` as a stable, citable name for "the
REPL app" — keeping `ReplApp` resolvable at `klorb.tui.ReplApp` (not `klorb.tui.app.ReplApp`)
costs nothing (`.flake8`'s existing `__init__.py:F401,F403` per-file-ignore already expects
`__init__.py` files in this codebase to re-export) and gives every one of those existing
docstring citations a single mechanical find/replace target
(`klorb.tui.repl.ReplApp` → `klorb.tui.ReplApp`) instead of needing to know which specific
new submodule each method landed in. Everything else — `PromptInput`, the widget-id
constants, `format_token_count`, `_handle_repl_crash`, etc. — is an internal implementation
detail with no re-export; the two existing tests that import extras directly from
`klorb.tui.repl` (`test_ask_user_questions_repl.py`, `test_escalate_privileges_repl.py`)
update their imports to the specific new submodule instead (see "Test layout" below).

Because `klorb.tui.repl` stops existing as a dotted path entirely under this layout (there is
no `repl` submodule to re-export from anymore), every docstring across the codebase that
cites `klorb.tui.repl.*` needs updating, not just the ones inside `klorb/tui/` itself — see
"Docs" under "Mechanics" below.

### `_base.py` sketch

```python
class ReplAppBase(App[None]):
    """Attribute/method declarations shared by every ReplApp mixin, so each mixin file
    type-checks on its own despite referencing state that other mixins set up. Carries no
    behavior; ReplApp(mixins..., ReplAppBase) is the only class actually instantiated.
    """

    _session: Session
    _process_config: ProcessConfig
    # ... one line per attribute `__init__` sets, plus any attribute set outside __init__
    # (e.g. inside on_mount) that a different mixin's method reads.
```

Populate this by grepping `__init__` (and any other assignment site) for `self\.\w+\s*=` once
the core/lifecycle group has been extracted into `app.py`, not by re-deriving it from memory.
Each mixin then reads `from klorb.tui._base import ReplAppBase` and declares
`class KeyActionsMixin(ReplAppBase):`. `app.py`'s `ReplApp` is the only class with a real
`__init__` body; mixins have none.

### Where `SelectionSafeScreen` lives

`SelectionSafeScreen.action_copy_text` does `isinstance(self.app, ReplApp)` and calls
`ReplApp._note_ctrl_c_copy()`, and `ReplApp.get_default_screen()` returns
`SelectionSafeScreen()` — the two classes reference each other. Rather than introduce a
circular import between two files to keep them "cleanly" separated, keep `SelectionSafeScreen`
defined in `app.py` right above `ReplApp`, exactly where it already sits relative to `ReplApp`
today (line 1181, immediately before line 1222). This is the one widget class from the
"before `ReplApp`" block above that does *not* move to `widgets/`.

## Target test layout

```
klorb/tests/tui/
    __init__.py
    conftest.py                    # fixtures/helpers currently at the top of
                                    #   test_tui_repl.py (lines ~1-236): _session,
                                    #   _session_with_tools, _reply, _tool_call_reply,
                                    #   _risk_report_reply, _wait_until, _focused_id,
                                    #   the autouse `_user_config_present`/`stub_force_exit`
                                    #   fixtures, etc. — shared across most of the files below.
    test_app.py                     # ReplApp lifecycle/compose/init tests, and any test that
                                    #   is genuinely cross-cutting (exercises >1 mixin's
                                    #   behavior in one flow) rather than owned by one module
    test_formatting.py
    test_entrypoint.py
    mixins/
        __init__.py
        test_key_actions.py
        test_workspace_bootstrap.py
        test_status_bar.py
        test_prompt_submission.py
        test_rendering.py
        test_interactions.py
    widgets/
        __init__.py
        test_prompt_input.py
        test_tool_call_widgets.py
        test_status_widgets.py
        test_palette.py             # moved from klorb/tests/test_palette.py
    panels/
        __init__.py
        test_ask_user_questions_panel.py     # renamed/moved from
                                              #   klorb/tests/test_ask_user_questions_screen.py,
                                              #   no content changes
        test_confirm_screen.py
        test_escalate_privileges_panel.py    # NEW — see below, EscalatePrivilegesPanel has
                                              #   no isolated test file today
        test_permission_ask_panel.py
    commands/
        __init__.py
        test_init_commands.py           # moved from klorb/tests/
        test_model_commands.py          # moved from klorb/tests/
        test_model_info_commands.py     # moved from klorb/tests/
        test_session_commands.py        # moved from klorb/tests/
        test_theme_commands.py          # moved from klorb/tests/
        test_thinking_commands.py       # moved from klorb/tests/
        test_trust_commands.py          # moved from klorb/tests/
    test_shell.py                       # moved from klorb/tests/test_shell.py
    test_ask_user_questions_integration.py   # moved from
                                              #   klorb/tests/test_ask_user_questions_repl.py
                                              #   (imports ReplApp + AskUserQuestionsPanel
                                              #   together — genuinely an integration test,
                                              #   not owned by one module)
    test_escalate_privileges_integration.py  # moved from
                                              #   klorb/tests/test_escalate_privileges_repl.py
                                              #   (same reasoning)
```

`klorb/tests/test_escalate_privileges.py` tests `klorb.tools.escalate_privileges.*` (checked:
`EscalatePrivilegesTool`/`validate_scope`), an unrelated module despite the similar filename —
out of scope for this plan, stays exactly where it is.

### `EscalatePrivilegesPanel` needs a new isolated test file, not just a move

Checked `test_escalate_privileges_repl.py`'s own docstring: "End-to-end tests: an
`EscalatePrivileges` tool call drives `EscalatePrivilegesPanel` through a real `ReplApp`,
mirroring `test_ask_user_questions_repl.py`." All four of its tests build a real `Session` +
`ReplApp`, submit a prompt, and drive a full turn — there is currently no test of
`EscalatePrivilegesPanel` in isolation the way `test_ask_user_questions_screen.py` tests
`AskUserQuestionsPanel` (a small `_AskUserQuestionsTestApp(App[None])` scaffold that mounts
the panel directly and drives its own rendering/keyboard-navigation, with no `Session` or
`ReplApp` involved at all).

So `panels/test_escalate_privileges_panel.py` is **net-new test-writing, not a mechanical
extraction** — there's no existing code to cut from. Build it the same way
`test_ask_user_questions_screen.py` is built: a minimal scaffold `App[None]` that mounts
`EscalatePrivilegesPanel` directly, covering its rendering (header/option text for a given
`EscalatePrivilegesContext`) and keyboard navigation/dismissal (`action_confirm`,
`action_decline`, escape) in isolation. `test_escalate_privileges_repl.py`'s four existing
tests still move to `test_escalate_privileges_integration.py` unchanged — they're the
`ReplApp`-driven end-to-end coverage that stays valuable alongside the new panel-only tests,
exactly mirroring how `test_ask_user_questions_repl.py` and `test_ask_user_questions_screen.py`
coexist today. Read `permission_ask_panel.py`'s and `escalate_privileges_panel.py`'s own
`compose()`/action methods before writing this file, so the new coverage reflects the panel's
actual current widget IDs/structure rather than guessing from the integration tests' assertions.

Every new test directory gets an `__init__.py` (empty, just a package marker) even though
`pytest`'s default rootdir-based discovery doesn't strictly require one — this repo's
`tests/fixtures/` subpackage already does this, and it avoids the "two files with the same
basename in different directories" import-mode ambiguity pytest's `prepend` import mode is
prone to as the tree grows.

## Mechanics: how to actually move the code

The instruction behind this plan is explicit: don't have an agent read 8,000+ lines and
retype them into new files from its own understanding — that's how a dropped `await`, a
silently-renamed local, or a reordered CSS rule sneaks in unnoticed. Everything below is
built around cut/paste tooling instead.

1. **Whole-file moves first** (`palette.py` → `widgets/`, the four panels → `panels/`, the
   seven command providers → `commands/`; `shell.py` needs no move). These are
   zero-content-risk: `git mv old/path new/path`, then a mechanical `import`-path rewrite
   everywhere that referenced the old path (`grep -rl` for the old dotted path across
   `klorb/src` and `klorb/tests`, then fix each hit — this is find/replace on import lines,
   not code retyping). Run `make lint typecheck test` after this batch before touching
   `repl.py` at all, so any import breakage is caught while the diff is still small and easy
   to reason about.

2. **Extracting a contiguous class or method group from `repl.py`** (every widget class, and
   each of the eight `ReplApp` method groups): use line-range extraction, not manual
   selection-and-retype.
   * Re-run `grep -n "^class \|^    def \|^    async def \|^    @"` against the *current*
     `repl.py` to get up-to-date line numbers immediately before each extraction — line
     numbers drift as earlier groups are removed from the file, so don't work from the
     approximate ranges in this plan once extraction has started.
   * Pull the exact range with `sed -n 'START,ENDp' repl.py > /tmp/chunk.py`, prepend the
     new file's copyright header + docstring + a hand-written (small, ~10-30 line) import
     block, and write it out as the new module — the *code body* itself is `sed` output, not
     retyped.
   * For a mixin file specifically: wrap the extracted method block in
     `class FooMixin(ReplAppBase):` (methods keep their existing indentation, they're already
     indented one level for class-body membership — no re-indentation needed).
   * Delete the same line range from `repl.py` with `sed -i 'START,ENDd'` (or, more safely,
     recreate `repl.py` by concatenating the ranges you're keeping — either way, this is a
     line-based cut, not a retype).
   * Fix imports at the end of each extraction, not before: run `make lint`, let
     flake8/pyflakes report unused imports in the file the code left and undefined names in
     the file the code arrived at, and resolve each mechanically. Don't pre-guess which
     imports each new file needs.

3. **Constants and formatting helpers**: these are the one part of `repl.py` that isn't
   contiguous (see "Current shape" above — constants are scattered next to their first use
   throughout the file). Handle them with a dedicated pass, *after* all the class/method
   groups have moved: `grep -n "^[A-Z_].* = \|^_[A-Z_].* = "` against what's left of
   `repl.py` to enumerate every remaining module-level constant, then for each one, `grep -rn`
   its name across the new `klorb/tui/` tree to see how many of the new files reference it.
   Exactly one referencing file: move the constant to live in that file (colocate, don't
   centralize). Two or more: it goes in `constants.py`. This is the same "don't duplicate a
   constant across files" rule CLAUDE.md already states, applied to the split itself, and
   it's a mechanical grep-and-count decision, not a judgment call per constant.

4. **Test file split**: same shape of process, applied to `test_tui_repl.py`.
   * First, capture a baseline: `pytest --collect-only -q` piped to a file, so the exact set
     of 199 (or however many by then) test IDs can be diffed against the post-split
     collection to confirm nothing was lost or silently renamed into a collision.
   * The shared fixtures/helpers block (today's lines ~1–236) moves to `conftest.py`
     largely as one contiguous `sed` extraction, same as a source module.
   * For each remaining test function, decide its destination by what it exercises, found
     mechanically rather than read-and-judged: `grep` the test body for which mixin/module's
     methods it calls, patches (`patch("klorb.tui.repl.<name>"...)`), or which widget class it
     queries for (`query_one(..., PromptInput)` implies `widgets/test_prompt_input.py`, a
     `patch` targeting `_confirm_permission_ask` implies `mixins/test_interactions.py`, etc).
     A test that patches/queries across more than one destination module is a genuine
     integration test and belongs in `test_app.py` (or one of the two `*_integration.py`
     files already called out above) rather than being forced into a single-module home.
   * Extract each test function (and any `@pytest.fixture`-decorated helper used only by
     tests moving together) by line range the same way as source code. Update the
     `from klorb.tui.repl import (...)` block at the top of each new test file to import only
     the names that file actually uses, from wherever they now live under `klorb.tui.*`.
   * After the full split, re-run `pytest --collect-only -q`, diff test IDs against the
     baseline (module path changes are expected and fine; the *set of test names* and their
     count should be unchanged, plus whatever new tests were written for
     `test_escalate_privileges_panel.py`), then `make lint typecheck test`.

5. **Docs**: because `klorb.tui.repl` stops existing as a dotted path (everything now lives
   directly under `klorb.tui`), every citation of `klorb.tui.repl.*` needs updating, not just
   ones inside `klorb/tui/` — this is a bigger sweep than a same-package internal move would
   be:
   * `grep -rl "klorb\.tui\.repl\|klorb/src/klorb/tui" klorb/src klorb/tests` — living code
     (docstrings in `session.py`, `process_config.py`, `watchdog.py`, `risk_classifier.py`,
     `workspace/trust_manager.py`, `token_estimate.py`, plus whatever lands in the new
     `klorb/tui/` tree itself, and `test_process_config.py`/`test_schema_envelope.py`) — these
     must be updated (CLAUDE.md: docstrings describe current code, not history), and it's a
     mechanical `klorb.tui.repl.ReplApp` → `klorb.tui.ReplApp` (or the specific new submodule,
     for anything more specific than the class name) find/replace, not prose rewriting.
   * `grep -rl "klorb/src/klorb/tui\|klorb\.tui\." docs/specs/` — `docs/specs/terminal-repl.md`
     and the handful of other specs that cite the old flat-file path need the same path
     update, since specs describe current state (CLAUDE.md's spec-writing rule).
   * `docs/adrs/` entries that mention `klorb.tui.repl.ReplApp` are historical decision
     records, not living docs — leave them as-is, same as `docs/plans/archive/` is never
     touched after the fact.
   * Write one new ADR (`docs/adrs/split-repl-app-into-mixins-not-composition.md` or similar)
     recording the mixin-vs-composition decision from "Current shape of `repl.py`" above,
     since that's exactly the kind of framework-level architecture decision this codebase's
     ADR convention exists for.

## Suggested execution order

1. Whole-file moves of the 11 already-separate `klorb/tui/*.py` modules into
   `klorb/tui/{panels,commands,widgets}/` (step 1 above) + matching test file relocations for
   the ones with existing 1:1 test files (`test_palette.py`, `test_init_commands.py`,
   `test_model_commands.py`, `test_model_info_commands.py`, `test_session_commands.py`,
   `test_theme_commands.py`, `test_thinking_commands.py`, `test_trust_commands.py`,
   `test_shell.py`, `test_ask_user_questions_screen.py` → `test_ask_user_questions_panel.py`).
   Verify with `make lint typecheck test`.
2. Extract the widget classes (`PromptInput`, `ToolCallLimitScreen`/`ToolCallStatic`/
   `RunningToolCallStatic`, `PaletteHint`/`PermissionBadge`) out of `repl.py` into
   `widgets/`. Verify.
3. Write `_base.py` (`ReplAppBase` attribute stubs) once `ReplApp.__init__` is the only thing
   left near the top of what remains of `repl.py`, so the attribute list can be read straight
   off it.
4. Extract the seven `ReplApp` method groups into `mixins/`, one group at a time, verifying
   (`make lint typecheck test`) after each — not as one big-bang extraction. This is the
   highest-risk part of the plan; doing it incrementally means a break is traceable to one
   group's extraction, not an 2,000-line diff.
5. What's left in `repl.py` (core/lifecycle + `SelectionSafeScreen` + `CSS`/`BINDINGS`) becomes
   `app.py`; `_handle_repl_crash`/`run_repl` become `entrypoint.py`; `repl.py` itself is
   deleted (`git mv` won't apply cleanly here since it's fanning out to many files — delete
   once everything's confirmed extracted). Write `__init__.py`, and update `cli.py`'s import.
6. Constants/formatting sweep (step 3 of "Mechanics").
7. Split `test_tui_repl.py` (step 4 of "Mechanics"), including writing the new
   `panels/test_escalate_privileges_panel.py` coverage, same incremental-with-verification
   approach as step 4 here.
8. Docs sweep + new ADR (step 5 of "Mechanics").
9. Final full-repo `make lint typecheck test` pass.
