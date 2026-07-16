# Split `klorb/tui/repl.py` into a `klorb.tui.repl` package

Claude: this plan is a **draft**, not ready for implementation. It lays out a target module
layout and, per the request that produced it, the *mechanics* of how to move the code (so the
move is done with cut/paste tooling, not retyped from context). The mixin-based split of
`ReplApp` proposed below is a new pattern for this codebase and should get an explicit
go/no-go during the pre-implementation architecture review, not be taken as locked in. Do not
implement any part of this until it's moved to `ready/`.

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
import. That means everything currently under `klorb/tui/` is, in practice, REPL
implementation detail — there is no second consumer yet that would justify keeping any of it
at the `klorb.tui` level instead of under `klorb.tui.repl`.

## Goals

* Nothing in `klorb/tui/` should be an "enormous" file by this codebase's existing norms
  (most source files here are under a few hundred lines; `permission_ask_panel.py` at 618
  lines is the biggest thing that *isn't* `repl.py` today).
* One source file, one test file (`docs/plans/README-PLANS.md` conventions still apply: no
  behavior change, this is pure restructuring).
* `klorb.tui.repl.run_repl` keeps working unchanged for `cli.py` — this is a structural
  refactor, not a public-API change.
* The move is mechanical: line ranges get cut from the old files into new ones with
  `sed`/sentinel-based extraction, not retyped by an agent reading and reproducing the code.
  Retyping ~8,300 lines of Textual/asyncio code from an LLM's own context is exactly how
  subtle bugs (a dropped `await`, a renamed local, a reordered CSS rule) get introduced
  without anyone noticing.

## Non-goals

* No behavior change. This plan does not touch what the REPL does, only where its code lives.
* No change to `klorb/tui/`'s two real external touch points: `cli.py`'s
  `from klorb.tui.repl import run_repl` and the CSS/keybindings the user sees.
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
  which mixin `foo` physically lives in, so there is no call-site rewriting at all. This is
  the recommended approach below.

The cost of mixins is typing: this repo's `make typecheck` runs mypy with
`--disallow-untyped-calls`, `--disallow-untyped-globals`, and `--disallow-redefinition`, so a
mixin method referencing `self.some_attr_set_by___init__` needs `some_attr` to be visible on
`self`'s declared type from that file's point of view — mypy doesn't know that whichever
concrete class eventually mixes this class in will also mix in the one that sets it. The fix
is a shared, attribute-only typed base class described below.

## Target source layout

```
klorb/src/klorb/tui/repl/
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
        ask_user_questions_panel.py    # moved from klorb/tui/
        confirm_screen.py               # moved from klorb/tui/
        escalate_privileges_panel.py    # moved from klorb/tui/
        permission_ask_panel.py         # moved from klorb/tui/
    commands/
        __init__.py
        init_commands.py         # moved from klorb/tui/
        model_commands.py        # moved from klorb/tui/
        model_info_commands.py   # moved from klorb/tui/
        session_commands.py      # moved from klorb/tui/
        theme_commands.py        # moved from klorb/tui/
        thinking_commands.py     # moved from klorb/tui/
        trust_commands.py        # moved from klorb/tui/
    shell.py                     # moved from klorb/tui/ (no natural group, small, stays flat)
```

`klorb/src/klorb/tui/__init__.py` stays as the (currently 2-line) package marker; nothing
else remains directly under `klorb/tui/` once this is done, since — per the "Context" section
above — nothing outside `klorb.tui.repl` consumes any of it today. If a second consumer of one
of these modules shows up later (e.g. a settings screen that isn't the REPL wanting
`ConfirmScreen`), promote that specific module back up to `klorb/tui/` at that time; don't
pre-emptively split now for a consumer that doesn't exist (see CLAUDE.md's guidance against
designing for hypothetical future requirements).

### Why `panels/` and `commands/` groupings

`ask_user_questions_panel.py`, `confirm_screen.py`, `escalate_privileges_panel.py`, and
`permission_ask_panel.py` are all "modal-ish interactive confirmation UI mounted into
`#interaction-panel`" — the same conceptual group `docs/specs/terminal-repl.md`'s
"Interaction panel" section already describes as one thing. `init_commands.py`,
`model_commands.py`, `model_info_commands.py`, `session_commands.py`, `theme_commands.py`,
`thinking_commands.py`, and `trust_commands.py` are all `SystemCommandProvider` subclasses
feeding `ReplApp.COMMANDS` (Textual's built-in command palette) — same shape, same
consumer, natural to browse together. Grouping them into subpackages is a placement call, not
a mechanical necessity; if the architecture review would rather keep all 11 of these flat
under `klorb/tui/repl/`, that's a smaller, equally valid variant of this plan.

### Public API surface (`__init__.py`)

Only `ReplApp` and `run_repl` are re-exported from `klorb/tui/repl/__init__.py`. `run_repl`
is the real external contract (`cli.py` imports it). `ReplApp` isn't imported anywhere outside
`klorb/tui/` and its tests today, but dozens of docstrings elsewhere in the codebase refer to
`klorb.tui.repl.ReplApp.<method>` as a stable, citable name for "the REPL app" — keeping
`ReplApp` resolvable at that path costs nothing (`.flake8`'s existing
`__init__.py:F401,F403` per-file-ignore already expects `__init__.py` files in this codebase
to re-export) and avoids a wave of edits to unrelated files' docstrings. Everything else —
`PromptInput`, the widget-id constants, `format_token_count`, `_handle_repl_crash`, etc. — is
an internal implementation detail with no re-export; the two existing tests that import
extras directly from `klorb.tui.repl` (`test_ask_user_questions_repl.py`,
`test_escalate_privileges_repl.py`) update their imports to the specific new submodule
instead (see "Test layout" below).

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
Each mixin then reads `from klorb.tui.repl._base import ReplAppBase` and declares
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
klorb/tests/tui/repl/
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
        test_confirm_screen.py
        test_permission_ask_panel.py    # subsumes the non-integration parts of today's
                                          #   inline permission-ask-screen tests
        # test_ask_user_questions_screen.py and the ask-user-questions/escalate-privileges
        # *_repl.py integration tests: see below, these do not move into panels/.
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

`klorb/tests/test_ask_user_questions_screen.py` (259 lines, tests `AskUserQuestionsPanel` in
isolation, no `ReplApp`) is the one existing sibling test file that's already correctly scoped
to a single panel module and just needs renaming/relocating to
`klorb/tests/tui/repl/panels/test_ask_user_questions_panel.py`; no content surgery needed.
There is no equivalent already-isolated test file for `EscalatePrivilegesPanel` —
`klorb/tests/test_escalate_privileges.py` (checked: it tests
`klorb.tools.escalate_privileges.escalate_privileges`/`.common`, unrelated to the TUI panel)
is out of scope for this plan. `test_escalate_privileges_repl.py`'s tests are the only
existing coverage of `EscalatePrivilegesPanel`, and per the grep at the top of this plan they
exercise it together with `ReplApp`, so `panels/test_escalate_privileges_panel.py` starts
empty/nonexistent unless the split of `test_escalate_privileges_repl.py` turns up individual
tests that don't actually need a live `ReplApp` and can be pulled out into panel-only
coverage.

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

1. **Whole-file moves first** (`palette.py`, `shell.py`, the four panels, the seven command
   providers). These are zero-content-risk: `git mv old/path new/path`, then a mechanical
   `import`-path rewrite everywhere that referenced the old path (`grep -rl` for the old
   dotted path across `klorb/src` and `klorb/tests`, then fix each hit — this is find/replace
   on import lines, not code retyping). Run `make lint typecheck test` after this batch before
   touching `repl.py` at all, so any import breakage is caught while the diff is still small
   and easy to reason about.

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
   its name across the new `klorb/tui/repl/` tree to see how many of the new files reference
   it. Exactly one referencing file: move the constant to live in that file (colocate, don't
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
     the names that file actually uses, from wherever they now live.
   * After the full split, re-run `pytest --collect-only -q`, diff test IDs against the
     baseline (module path changes are expected and fine; the *set of test names* and their
     count should be unchanged), then `make lint typecheck test`.

5. **Docs**: `docs/specs/terminal-repl.md` and the handful of other specs that cite
   `klorb/src/klorb/tui/repl.py`'s old flat-file path (`grep -rl "klorb/src/klorb/tui\|klorb\.tui\."
   docs/specs/`) need their file-path references updated to match, since specs describe
   current state (CLAUDE.md's spec-writing rule). `docs/adrs/` entries that mention
   `klorb.tui.repl.ReplApp` are historical decision records, not living docs — leave them
   as-is, same as `docs/plans/archive/` is never touched after the fact. Write one new ADR
   (`docs/adrs/split-repl-app-into-mixins-not-composition.md` or similar) recording the
   mixin-vs-composition decision from "Current shape of `repl.py`" above, since that's exactly
   the kind of framework-level architecture decision this codebase's ADR convention exists
   for.

## Suggested execution order

1. Whole-file moves of the 11 already-separate `klorb/tui/*.py` modules into
   `klorb/tui/repl/{panels,commands,widgets}/` (step 1 above) + matching test file
   relocations for the ones with existing 1:1 test files (`test_palette.py`,
   `test_init_commands.py`, `test_model_commands.py`, `test_model_info_commands.py`,
   `test_session_commands.py`, `test_theme_commands.py`, `test_thinking_commands.py`,
   `test_trust_commands.py`, `test_shell.py`, `test_ask_user_questions_screen.py`). Verify
   with `make lint typecheck test`.
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
   once everything's confirmed extracted). Write `__init__.py`.
6. Constants/formatting sweep (step 3 of "Mechanics").
7. Split `test_tui_repl.py` (step 4 of "Mechanics"), same incremental-with-verification
   approach as step 4 here.
8. Docs sweep + new ADR (step 5 of "Mechanics").
9. Final full-repo `make lint typecheck test` pass.

## Open questions for architecture review

* **Mixins vs. flat-but-large.** This plan's central bet is that splitting `ReplApp` itself
  (not just what surrounds it) is worth the mixin-typing machinery. A smaller, safer variant
  of this plan would leave `ReplApp` as one big class in `app.py` (still shrinking `repl.py`
  from 3,456 to ~2,600 lines just by moving the widgets and other files out) and stop there.
  Given the task explicitly asked for `repl.py` to be broken into "sensible submodule
  chunks" and a ~2,600-line single class would still read as "enormous" by this codebase's
  own norms, this plan recommends going all the way to mixins — but it's a real trade-off
  (new pattern, more files, `_base.py` bookkeeping) worth a deliberate yes/no rather than
  silent default.
* **`panels/`/`commands/`/`widgets/` subpackages vs. flat.** Called out inline above — not
  load-bearing, easy to change either way at implementation time.
* **Whether `EscalatePrivilegesPanel` has an existing isolated test file** — flagged above as
  something to verify before assuming `test_escalate_privileges.py` slots into `panels/`
  unchanged; it didn't show up in the `klorb.tui` import grep this plan was based on, so it
  may test something else entirely (e.g. the session-level escalate-privileges flow rather
  than the panel widget).
