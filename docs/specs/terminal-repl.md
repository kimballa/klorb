# Terminal REPL

## Summary

Running `klorb` with no `-m`/`--message` argument starts an interactive, full-screen
terminal REPL instead of the single-shot prompt/response path. The REPL shows a vertically
scrolling history of prompts and responses, with a text input box pinned to the bottom of the
screen. The user types a prompt, hits enter, and it is submitted: the prompt scrolls up into
the history, the response streams in below it once the model replies, and the input box is
ready for the next prompt. See [[use-textual-for-the-terminal-ui]] for why
[Textual](https://github.com/Textualize/textual) was chosen as the underlying TUI framework.

## How it works

* `klorb.tui.ReplApp` (`klorb/src/klorb/tui/app.py`), a `textual.app.App` subclass, is
  assembled from mixins under `klorb/src/klorb/tui/mixins/` (key handling/quit/watchdog,
  workspace bootstrap/trust, status bar, prompt submission, rendering, and interaction
  flows), each holding one cohesive slice of its methods verbatim; `app.py` itself keeps only
  `CSS`/`BINDINGS`/`COMMANDS`, `__init__`, `compose`, and model/theme/thinking selection.
  `klorb.tui.run_repl(session, initial_message, session_log_enabled)`
  (`klorb/src/klorb/tui/entrypoint.py`) is a thin function that constructs and runs it;
  `klorb.tui`'s own `__init__.py` re-exports both `ReplApp` and `run_repl` as the package's
  only public surface. `ReplApp` takes a [[session-and-turns]] `Session`
  (constructing a default one if none is given) rather than a raw `ApiProvider`/model pair,
  so the REPL sends every turn through the same `Session.send_turn()` path a one-shot
  prompt uses. `session_log_enabled` records whether `cli.main()` turned on per-session
  logging for this invocation, so clearing the session (below) knows whether to roll the log
  file over.
* `ReplApp.compose()` lays out six widgets/regions top-to-bottom: a `Header` showing the
  current workspace and model (see below), a `VerticalScroll` (id `history`) that
  holds the conversation so far, a `Vertical` (id `interaction-panel`) that stays empty (and,
  with `height: auto`, invisible) except while a permission ask or `AskUserQuestions` prompt is
  active — see "Interaction panel" below — a `PromptPalette` (id `prompt-palette`, hidden until
  the user types a leading `>` — see [[command-palette-from-prompt]]), a `PromptInput` (id
  `prompt-input`) for typing the next prompt, and a `Horizontal` (id `status-row`) docked to
  the bottom of the screen that holds a `Static` `> palette` hint, the `Footer` (key
  bindings), a `PermissionBadge` (id `permission-badge`), and a `Static` token-tally widget
  (id `status-bar`) side by side in the same row — so both read like one more item alongside
  `^q Quit` rather than a separate line. The history container is styled `height: 1fr` so it
  fills all available vertical space above the input box, which is why the history scrolls
  "up" as content is added while the input box stays pinned to the bottom of the screen.
* **Interaction panel.** A permission ask (see docs/specs/permissions.md's "Interactive `"ask"`
  confirmation" section) or an `AskUserQuestions` prompt (see [[ask-user-questions]]) renders as
  a full-width band mounted into `#interaction-panel`, between the history scroll and the
  prompt input, rather than as a floating modal dialog — see
  docs/adrs/embed-tool-approval-and-ask-user-questions-in-history-panel.md for why. Concretely,
  `ReplApp` mounts a `klorb.tui.panels.permission_ask_panel.PermissionAskPanel` or
  `klorb.tui.panels.ask_user_questions_panel.AskUserQuestionsPanel` into `#interaction-panel` and
  `await`s an `asyncio.Future` its `on_dismiss` callback resolves — see those modules' own
  docstrings for each panel's own content/keyboard-navigation shape (the Allow/Deny × scope
  grid, the options list, the `+`/`[more...]` full-screen command expansion, and so on, which
  are unchanged from before this layout became non-modal). While a panel is active
  (`ReplApp._enter_interaction_mode()`), the prompt input is disabled, visually muted (`color:
  $text-muted`), and collapsed to `height: 1` via the `interaction-active` CSS class — shrinking
  a multi-line draft back down to its default single-row size (without discarding the draft
  text itself, which is exactly what it was before once the class is removed) so the panel has
  the vertical room it needs. `ReplApp._exit_interaction_mode()` undoes all of that once the
  panel is dismissed. `ReplApp._record_interaction_history()` then mounts a small permanent
  record into the history scroll — the panel's header line, the command/path/question body it
  showed, and `"Decision: ..."` — so scrolling back through the session later shows not just
  that an approval or a question happened, but what was asked and what was decided, in context
  with the rest of the conversation.
* **Task sidebar.** `Ctrl+T` (`TaskSidebarMixin.action_toggle_task_sidebar`, in
  `klorb.tui.mixins.task_sidebar`) shows or hides `klorb.tui.widgets.task_sidebar.TaskSidebar`
  (id `task-sidebar`), a `dock: right`, fixed-width panel listing this session's chainlink todo
  items — see [[chainlink-task-tracking]]. Hidden (`display: none`) until first toggled on, at
  which point it refreshes immediately: `fetch_and_sort_issues(client, include_closed=True)`
  runs on a `@work(thread=True)` worker (`TaskSidebarMixin._refresh_task_sidebar`), since
  `ChainlinkClient` shells out to the `chainlink` binary synchronously, and the result reaches
  the widget via `call_from_thread`. Every finished tool call in a turn
  (`PromptSubmissionMixin.handle_tool_call`) also calls
  `_maybe_refresh_task_sidebar_after_tool_call`, which re-fetches only while the panel is
  visible and only for `ToolCallEvent.name` values that can change the list or the current
  tracked task (`TodoList`/`TodoNext`/`TodoCreate`/`TodoUpdate`). Each row reads `#id title`; a
  closed issue renders dim and struck-through (Rich style `"dim strike"` on that row's own
  `rich.text.Text`); whichever issue's `id` matches `Session.cur_chainlink_task_id` gets a
  leading `★` in place of the usual two-space indent. If the `chainlink` binary can't be found,
  or the refresh otherwise fails (`ChainlinkError`/`ValueError`), the panel shows "Task tracking
  is not available in this workspace." instead of a list.
* `PermissionBadge` (`klorb.tui.widgets.status_widgets`) shows the session's current
  `Session.config.permission_framework` value bracketed and right-justified (`[ask]`,
  `[auto]`, or `[deny]`) within a fixed-width cell (`PERMISSION_BADGE_WIDTH` — the longest
  value plus one, plus `PERMISSION_BADGE_HORIZONTAL_PADDING` since Textual's CSS `width` is
  a border-box measurement that already includes the badge's own `padding: 0 1`) so a
  shorter value is left-padded rather than a longer one ever clipping its trailing `]`.
  `ReplApp._update_permission_badge()` sets the initial value (no flash) from `on_mount()`.
  Shift+Tab cycles it through `PERMISSION_FRAMEWORK_CYCLE` (`"ask"` → `"auto"` → `"deny"` →
  back to `"ask"`) via `ReplApp.action_cycle_permission_framework()`, bound as a
  `priority=True` app-level binding in `ReplApp.BINDINGS`. A priority binding is checked from
  the `App` down before the key event is forwarded to whatever widget currently has focus, so
  it fires regardless of focus state -- including while `PromptInput` is disabled and blurred
  during an in-flight turn or an open interaction panel, exactly when a user is most likely to
  want to flip the framework. `action_cycle_permission_framework()` flashes the badge: a
  quick 150ms spark of bright yellow (the same `$footer-key-foreground` as the
  footer's own key-binding chips), then a longer 400ms glow of bright/bold white, before
  settling back to its normal color — an intentionally uneven two-step cadence (see
  `PermissionBadge._FLASH_YELLOW_SECONDS`/`_FLASH_WHITE_SECONDS`), since two equal-length
  steps read as mechanical rather than like a natural attention flash. See
  docs/specs/permissions.md's "Interactive `"ask"` confirmation" section for what each
  value means.
* `ReplApp.format_title()` overrides `App.format_title()` to control what the `Header`
  displays, rather than relying on the default `title — sub_title` join: it shows the current
  workspace's path (`SessionConfig.workspace.path`), shortened to its last two path components
  prefixed with `"..."` (e.g. `".../last/two_parts"`) once the full path exceeds
  `_WORKSPACE_PATH_DISPLAY_MAX_CHARS` characters, followed by `" (Untrusted)"` when
  `SessionConfig.workspace.trusted` is `False`, then `" - "` and the active model
  (`self.sub_title`, kept in sync by `select_model()`), with its thinking effort level in
  parentheses appended whenever thinking is enabled — e.g.
  `".../path/to/somewhere (Untrusted) - gpt-4o (High)"`. Since `Header` only redraws when
  `App.title`/`App.sub_title` change (its own reactive watchers), state changes it depends on
  but that don't touch those two attributes — trusting a workspace (`_apply_workspace_config()`)
  or toggling thinking enablement/effort (`set_thinking_enabled()`/`set_thinking_effort()`) —
  call `ReplApp._refresh_header_title()`, which uses `mutate_reactive()` to force `Header` to
  re-invoke `format_title()` without needing `sub_title`'s value to actually change.
* `Footer`'s own `dock: bottom` CSS rule is overridden to `dock: none; width: 1fr` when
  nested inside `#status-row`, since Textual resolves a docked widget's position against
  its immediate parent: left docked, `Footer` would claim the entire row's height for
  itself (its dock arrangement is computed independently of siblings) and collide with
  `#status-bar`/`#permission-badge` instead of sharing the row. With the dock removed,
  `Footer` behaves as a normal flex child that takes up the remaining space next to its
  fixed-width siblings.
* The status bar shows a running token tally as `"<used> / <limit>"` (e.g. `"1.4k / 128k"`),
  where `<used>` is `Session.total_tokens_used()` (the sum of `num_tokens` across every
  message recorded so far — see [[session-and-turns]]) and `<limit>` is
  `Session.max_context_window()`, read from the active model's
  `Model.capabilities()["max_context_window"]` (see [[model-framework]]). If the active
  model isn't registered (so its context window is unknown), the bar shows just `<used>`
  with no `" / <limit>"` suffix. Both numbers are rendered by `format_token_count()`, which
  shows raw integers under 1000 and otherwise an SI suffix (`k`/`M`/`B`) rounded to 2
  significant figures — e.g. `1400` -> `"1.4k"`, `423000` -> `"420k"` — so the bar stays
  short and doesn't imply more precision than the token accounting actually has (see
  [[derive-user-turn-token-counts-from-a-prompt-token-delta]]). `ReplApp._update_status_bar()`
  recomputes and redraws the bar on mount, after switching models, after clearing the
  session, and at the end of every turn (success or error).
* The input box's default full box border is overridden (`border: none; border-top: solid
  $accent;`) so only a single horizontal rule separates it from the history, with no side
  or bottom borders. This keeps the input looking like a plain line of text rather than a
  boxed-in widget, and avoids visually implying that the surrounding text isn't selectable.
  `border_title` is set to `"message"`, which Textual renders embedded in that top rule
  (left-aligned by default), e.g. `─message────────────────`.
* On mount, the input box is labeled and focused so the user can start typing immediately.
  If `ReplApp` was constructed with `initial_message`, it's then submitted automatically as
  the first turn, exactly as if the user had typed and entered it — this is how `klorb -m
  "..." --interactive` makes a starting message "the first thing the user said" while
  staying in the REPL afterward.
* `ReplApp._submit_prompt()` is the shared path for both user-typed and initial-message
  turns:
  1. Disables the input box (so a second prompt can't be submitted while one is in flight).
  2. Mounts a `Static` widget showing the prompt text (styled via the `.prompt` CSS class)
     at the bottom of the history, and scrolls the history to the end.
  3. Dispatches the prompt to `Session.send_turn()` (see [[session-and-turns]]) on a
     background thread, via a `@work(thread=True)` worker, so the UI event loop stays
     responsive while waiting on the network call.
  When the user presses enter in the input box (`Input.Submitted`), `ReplApp` ignores the
  event if the trimmed value is empty, otherwise clears the input box and calls
  `_submit_prompt()`.
  The response renders progressively as it streams: `_send_prompt`'s worker passes an
  `on_chunk` callback into `Session.send_turn()` that accumulates the text seen so far and,
  via `call_from_thread`, mounts a `Markdown` widget on the first chunk (capturing the
  widget reference so later chunks call `.update()` on the same widget instead of mounting
  new ones). Once `send_turn()` returns, the widget gets one final `.update()` with the
  complete response text and the turn finishes; if nothing ever streamed (e.g. a
  non-streaming test double), it falls back to mounting a fresh `Markdown` widget with the
  full response instead. On failure, `_show_error` mounts a `Static` widget with the
  exception message (styled via the `.error` CSS class). Either way, the history is
  scrolled to the end again, and the input box is re-enabled and refocused.
* If the model streams reasoning/thinking deltas (see [[openrouter-prompt-client]] and
  [[session-and-turns]]'s `_reasoning_params()`), `_send_prompt` passes a second
  `on_thinking_chunk` callback into `Session.send_turn()` that mirrors the content-chunk
  handling but on its own accumulator: on the first thinking chunk, `_mount_thinking_widget`
  mounts a `Static` labeled `THINKING_LABEL` (`"<Thinking>"`, styled via the `.thinking-label`
  CSS class — left-justified above the block, not indented, like `.prompt`/`.error`)
  followed by a second `Static` (styled via the `.thinking-body` CSS class — `color:
  $text-muted` to match the dim `<Thinking>` label, and `padding: 0 2` to match the same
  2-column left indent the response gets for free from the `Markdown` widget's own default
  CSS, since a plain `Static` has none) whose content is the accumulated reasoning text
  verbatim; later chunks call `.update()` on that same `Static` with the growing text. The
  `Static` is constructed with `markup=False` so reasoning text containing literal `[`/`]`
  can't be misread as console markup, and the italic styling comes from the `.thinking-body`
  CSS class (`text-style: italic`) rather than inline markup — deliberately not a `Markdown`
  widget, since reasoning text commonly spans multiple paragraphs and Markdown's `*...*`
  emphasis doesn't apply across blank-line-separated blocks the way a whole-widget CSS style
  does (a `Markdown` widget was tried first and silently failed to italicize multi-paragraph
  reasoning; see [[render-thinking-body-as-rich-markup-not-markdown]] for that finding, and
  [[style-arbitrary-text-spans-with-content-not-escaped-markup]] for why the styling is CSS on
  a `markup=False` widget rather than `escape()`-d inline markup).
  There's no non-streaming fallback for the thinking block (unlike the response): if
  nothing ever streamed as reasoning, no thinking block is shown, since there'd be no text
  to show.
* Pressing Escape while a response is streaming in aborts it: `ReplApp` creates a fresh
  `threading.Event` per submitted prompt and passes it as `Session.send_turn()`'s
  `cancel_event`, and Escape (bound to `action_abort_response`, shown in the footer only
  while a turn is in flight via `check_action`) sets it. `_send_prompt`'s worker thread
  catches the `ResponseAborted` this raises and calls `_handle_aborted_response`, which
  leaves the echoed prompt and every widget mounted for that turn (partial
  response/thinking/tool-call widgets) in place — tagging whichever of the response/thinking
  widgets was still streaming with an "(interrupted)" marker, or mounting a standalone
  `.interrupted` marker if neither had started yet — and leaves the now-re-enabled input box
  empty rather than repopulating it with the original prompt. `Session` keeps the turn in
  `self.messages` too: the user `Message` and any partial assistant/thinking placeholder(s)
  are tagged `processing_state="aborted"` rather than removed, and any earlier round's
  completed `tool_use`/`tool_response` messages stay exactly as they would in a completed
  turn — see [[escape-aborts-streaming-turn-and-discards-it-from-history]] and
  [[keep-aborted-turn-content-in-history-tagged-not-discarded]].
* If a turn's tool calls (see [[tool-framework]] and [[session-and-turns]]) reach
  `SessionConfig.max_tool_calls_per_turn`/`max_tool_calls_per_session`, `Session` asks
  whether to double the reached cap and keep going via the `on_tool_call_limit_reached`
  callback `_send_prompt` passes into `Session.send_turn()`:
  `ReplApp._on_tool_call_limit_reached(message)`. Since that callback runs on the worker
  thread but showing a modal and waiting for its result requires the app's own event loop,
  it blocks via `self.call_from_thread(self._confirm_tool_call_limit, message)`, where
  `_confirm_tool_call_limit` is `async def` and `await`s
  `self.push_screen_wait(ToolCallLimitScreen(message))` — `push_screen_wait` suspends until
  the pushed screen calls `self.dismiss(...)`, which is exactly what happens when the worker
  thread's `call_from_thread` needs to block until the user answers. `ToolCallLimitScreen`
  is a `ModalScreen[bool]` showing `message` above a `Yes`/`No` button pair (`Yes` focused by
  default so Enter confirms); clicking `No` or pressing Escape dismisses with `False`.
  Returning `True` from the callback lets the turn continue (with that cap doubled);
  `False` makes `Session` raise `ToolCallLimitExceeded`, which `_send_prompt`'s existing
  failure handling renders the same way as any other turn error (`_show_error`).
* Every tool call the model makes (see [[tool-framework]]) shows up in the history as it
  happens: `_send_prompt` passes an `on_tool_call` callback into `Session.send_turn()`
  (`TurnEventHandlers.on_tool_call`), fired once per finished call — success or failure —
  from `Session._run_tool_calls`. `ReplApp._render_tool_call` turns the callback's
  `ToolCallEvent` (raw `name`/`args`/`result`/`error`, not pre-rendered text — see
  [the raw-callback-data ADR](../adrs/render-tool-calls-via-raw-callback-data.md)) into a
  `RenderedToolCall` (`summary_content`, `detail_content`, `on_click`) by instantiating the
  named tool a second time and calling its `summary()`/`detail_view()` (falling back to
  `default_tool_call_summary()`/`default_tool_call_detail()` if the name isn't a registered
  tool) — or, for a tool whose `diff_preview()`/`read_preview()` returns non-`None`, a colored/
  numbered `textual.content.Content` and a click-to-expand callback instead; see "Diff and read
  previews" below. `_mount_tool_call_widget` mounts a left-justified `<Tool use>` label
  (`TOOL_USE_LABEL`, styled via the `.tool-call-label` CSS class — the same label/body split
  `_mount_thinking_widget` uses for `<Thinking>`) followed by a `ToolCallStatic` (styled via the
  `.tool-call` CSS class) showing `summary_content`. `Ctrl+O` (`action_toggle_tool_call_detail`)
  globally toggles every `ToolCallStatic` currently in the history — from any turn, not just the
  latest — to `detail_content` and back, via an app-lifetime `_tool_call_detail_shown` flag
  rather than per-turn state, so the toggle persists across turns and clearing the session. A
  newly-mounted tool-call widget picks up whichever mode is currently active, rather than always
  starting as a summary. Tool-call widgets (both the label and the `ToolCallStatic`) mounted
  before an abort stay in the history and the toggle-tracking list exactly like any other turn's,
  since the tool calls they represent already ran to completion. The footer's own label for this binding
  flips with it — `"Detail"` while summaries are shown, `"Hide"` once detail is shown — by
  replacing the `"ctrl+o"` entry in `self._bindings.key_to_bindings` (this `ReplApp`
  instance's own mutable copy of the merged class-level `BINDINGS`) and calling
  `refresh_bindings()`, since a `Binding`'s description is otherwise fixed at `BINDINGS`
  class-definition time. Before a tool call finishes, a `RunningToolCallStatic` widget
  (inheriting from `ToolCallStatic`) is mounted with the tool's pre-execution summary (via
  `Tool.summary(args)` with no `result`) plus a crawling bold-character animation on the
  word "Running..." (see
  [[show-tool-calls-before-completion-with-running-indicator]]). The animation uses
  `Rich.text.Text` spans at 120ms per frame so the user knows the system hasn't frozen.
  When the tool completes, `finalize()` replaces the animated content with the final
  summary/detail content (and click callback) and stops the timer. The widget remains a
  `RunningToolCallStatic` instance in the DOM, so `history.query(ToolCallStatic)` and `Ctrl+O`
  both work unchanged. `_running_tool_call_widgets` (a `call_id`-keyed dict on `ReplApp`) links
  the "started" mount to the "completed" finalize; a tool call that was never started (e.g.
  malformed JSON arguments) falls back to the original `_mount_tool_call_widget` path.
* **Diff and read previews**: `EditFile`/`CreateFile` (and their `EditMemory`/`CreateMemory`/
  `EditScratchpad` counterparts) and `ReadFile`/`ReadMemory`/`ReadScratchpad`/`ReadSkillFile`
  render richer than plain text. `EditFileCore`/`CreateFileCore` (`klorb.tools.util`) compute a
  structured diff at apply time via `klorb.tools.util.diff_lines.build_diff_hunks()` —
  `DIFF_CONTEXT_LINES` (8) lines of unchanged context on either side of each change, nearby
  changes merged into one hunk — and store it as jsonable `DiffHunk`s in `result["diff"]`, so it
  survives session persistence and restore (`json.dumps`/`json.loads`) without ever re-diffing
  against a file that may have changed since. Each edit/create tool's `diff_preview()` override
  parses that back into a `klorb.tools.tool.DiffPreview`; each read tool's `read_preview()`
  override instead returns a `ReadPreview` — up to 4 numbered lines from the read's own captured
  content, plus a lazy `open_full()` closure that performs a fresh, passive re-read (no
  permission re-ask) only when the user actually clicks to expand, since the read range alone
  doesn't carry the whole file. `RenderingMixin._render_tool_result` turns a `DiffPreview` into a
  compact (`max_lines=8`, `render_diff_content`) `summary_content` and an uncapped
  `detail_content` for Ctrl+O — green `add`/red `del`/unstyled `context` lines with a
  right-aligned old/new line-number gutter, a trailing gutter-less `"..."` when the compact view
  is truncated. The compact view does *not* simply take the diff's first 8 lines: since a hunk's
  own leading context can itself run up to `DIFF_CONTEXT_LINES` lines, that would show nothing but
  context and never reach the change at all. Instead it starts only
  `_COMPACT_CONTEXT_BEFORE_LINES` (2) lines before the first changed line, so the compact preview
  always includes the change itself — the full leading context is still there in the uncapped
  Ctrl+O/overlay view. A `ReadPreview` renders into a plain numbered `summary_content`
  (`render_read_preview_content`), leaving `detail_content` as the tool's existing (unchanged)
  capped-at-8-lines `detail_view()` string. Clicking either
  widget (`ToolCallStatic.on_click`) pushes a `klorb.tui.panels.preview_screens` overlay —
  `DiffDetailScreen` reusing the already-built full `detail_content`, or `ReadDetailScreen`
  running `open_full()` at click time and scrolling to the read's `start_line` (an in-overlay
  "Could not reopen: ..." message if that fails, e.g. the file was since deleted). Both overlays
  are `ModalScreen`s dismissed with Escape, modeled on `ExpandedCommandScreen` (see the bash-tool
  spec) — a screen-stack push, not a blocking call, so the agent keeps running underneath and any
  `PermissionAskPanel` that needs to appear still mounts in its usual place and simply waits until
  the overlay is dismissed.
* A turn with tool calls is really a sequence of rounds under the hood (see
  [[session-and-turns]]'s `Session._dispatch_turn`): each round streams its own
  thinking/response text, then, if it ends in a tool-call request, `Session` dispatches those
  calls before starting the next round's stream. `_send_prompt` tracks this with a
  `round_index` counter, bumped every time `on_tool_call` fires (the signal that a round
  boundary has passed, since dispatch only happens between two rounds' streams, never mid-
  stream); `handle_chunk`/`handle_thinking_chunk` compare the round their current
  response/thinking widget belongs to against `round_index` and start a fresh widget rather
  than appending to the previous round's whenever it's moved on. Each round's thinking and
  response therefore render as their own blocks, in the order they actually happened, with
  that round's tool-call widgets sitting between one round's blocks and the next's — rather
  than one widget absorbing every round's text into a single ever-growing block that reads as
  older than the tool calls mounted after it started (see
  [the per-round-block ADR](../adrs/render-each-tool-call-rounds-thinking-and-response-as-its-own-block.md)).
  An aborted turn's
  `response_widget`/`thinking_widget` (see the Escape bullet above) are therefore always the
  ones belonging to the round that was still streaming when Escape fired, matching the single
  round's worth of content `Session` keeps for it.
* Each new block in the history — a submitted prompt (`.prompt`), a `<Thinking>` label
  (`.thinking-label`), a `<Tool use>` label (`.tool-call-label`), and a model response
  (`.response`, applied to the `Markdown` widget in both `_mount_response_widget` and
  `_show_response`) — carries a top-only margin (`margin: 1 0 0 0`) in its CSS class, so a
  blank line separates it from whatever was mounted before it regardless of that widget's
  type. `.thinking-body` and `.tool-call` (the *body* widgets, not their labels) carry no
  margin of their own, since the preceding label already opened the gap.
* `Ctrl+P`'s command palette also includes `ThinkingCommandProvider`
  (`klorb/src/klorb/tui/commands/thinking_commands.py`), listing `"Enable thinking"`, `"Disable
  thinking"`, and a single `"Set thinking effort"` command (rather than one palette entry
  per `ThinkingEffort` level, which cluttered the palette). Selecting `"Enable
  thinking"`/`"Disable thinking"` calls `ReplApp.set_thinking_enabled(bool)` directly, which
  mutates `Session.config.thinking_enabled` (same pattern as `select_model()`) and appends a
  `.notice` item to the history scroll confirming the change (see
  [[avoid-toasts-prefer-history-notices]]). Selecting `"Set thinking effort"` instead reads
  the current level via the new `ReplApp.get_thinking_effort()` getter and pushes
  `ThinkingEffortScreen`, a `ModalScreen` with a `"Thinking effort level:"` header `Static`
  above the three `ThinkingEffort` levels (`"low"`/`"medium"`/`"high"`) listed vertically in
  an `OptionList`,
  with the currently-active level's entry suffixed with `" *"`; the up/down arrow keys move
  the selection and Enter confirms it (`OptionList`'s built-in bindings), calling
  `ReplApp.set_thinking_effort(level)` and dismissing the modal, while Escape dismisses
  without changing anything. `"Enable thinking"`/`"Disable thinking"` remain an always-on/off
  pair, mirroring how `ModelCommandProvider` always lists every model rather than showing
  dynamic toggle-state labels.
* `ReplApp.clear_session()` — reached by typing `>clear` and pressing enter to select
  `Clear session` from the inline palette (see [[command-palette-from-prompt]]), or the
  equivalent `Ctrl+P` → `Clear session` — replaces the active `Session` with a new one: same
  `SessionConfig` (model carries over) and the same `provider`/`model_registry` instances
  (via `Session`'s read-only properties, so the OpenAI client and model discovery aren't
  rebuilt), but a fresh `generate_session_id()`, a fresh [[tool-framework]] `ToolRegistry`
  built from `ReplApp._process_config` and the new session's `SessionConfig` (unlike
  `provider`/`model_registry`, not reused — see
  [the fresh-instance-per-call ADR](../adrs/tool-registry-instantiates-a-fresh-tool-per-call.md)),
  and an empty message history. This runs synchronously — no worker thread, no disabling the
  input box, since no model call is involved. The visible history's children are removed, and
  if `session_log_enabled` is `True`, `configure_logging()` is called again with a new
  `session_log_path()` for the new session id (relying on `configure_logging`'s `force=True`
  behavior to safely repoint the root logger's file handler mid-process). See
  [[clear-command-starts-a-new-session-and-log-file]].
* `Ctrl+P`'s command palette (and `>init` from the inline palette) also includes
  `InitCommandProvider`'s `Init local klorb config`, which runs
  `klorb.klorb_init.run_init("user", force=False)` and reports the outcome via `App.notify()`.
  `ReplApp.on_mount()` mounts a `Static` (class `notice`) into the history on startup if
  `klorb.process_config.user_config_path()` doesn't exist yet, pointing the user at that
  command. See [[klorb-init]].
* Typing a line starting with `!` and pressing enter runs the rest of the line as a shell
  command instead of submitting a prompt — e.g. `!ls -la`. `ReplApp._submit_shell_command()`
  echoes `!command` into the history (styled like a submitted prompt) and disables the input
  box, then dispatches to `_run_shell_command`, a `@work(thread=True)` worker that mirrors
  `_send_prompt`'s streaming pattern: `klorb.tui.shell.UserShellCommand.run()` runs `command`
  via `ProcessConfig.shell_command -- --login -c command` (default `/bin/bash`; the shell
  binary path is a process-only, `klorb-config.json`-configurable setting — see
  [[process-and-session-config]]), pumping its stdout and stderr on their own background
  threads and calling back into a shared `handle_output` (guarded by a lock, since both pump
  threads call it concurrently) once per line as it arrives. The first line mounts a `Static`
  widget (not `Markdown`: shell output is plain text, and `Markdown`'s CommonMark rendering
  collapses a single newline inside a paragraph into a soft line break, mangling multi-line
  output); later lines `.update()` the same widget with the growing accumulated text. The
  `Static` is constructed with `markup=False` so literal `[`/`]` in the output (e.g. `[INFO]`
  log tags) render verbatim instead of being misread as console markup (see
  [[style-arbitrary-text-spans-with-content-not-escaped-markup]]). Only one shell command can
  be in flight at a time for a
  given REPL: the input box stays disabled for the duration, exactly as it does for a model
  turn, so a second `!command` can't be submitted while the first is still running.
  `ProcessConfig.shell_timeout_seconds` (`shell.timeout` on disk, default `None` — no limit)
  bounds how long a command may run before `UserShellCommand.run()` kills it and raises
  `ShellCommandTimedOut`; either that or a nonzero exit status is shown as an `.error`-styled
  `Static` in the history (mirroring `_show_error` for a failed model turn) once the command
  finishes.
* Pressing Ctrl+C while a shell command is running interrupts it instead of quitting:
  `ReplApp.action_interrupt()` (bound to `ctrl+c` in place of Textual's default `quit` action)
  sets the shell command's `threading.Event` if one is in flight — `UserShellCommand.run()`
  notices, kills the process, and raises `ShellCommandCancelled`, which is shown the same way
  a timeout is — and otherwise falls through to quitting the app, so Ctrl+C with no shell
  command running behaves exactly as it did before.
* `Ctrl+C` (when no shell command is running) and `Ctrl+Q` quit the REPL. `Ctrl+P` opens
  Textual's command palette, which includes `ModelCommandProvider` for switching the active
  model — see [[model-framework]]. Selecting a model updates `Session.config.model` directly.
  Typing `>` in the prompt input reaches the same providers without leaving the prompt box —
  see [[command-palette-from-prompt]]. `Ctrl+O` globally toggles every rendered tool call
  between its one-line summary and its fuller detail view (see above).
* `klorb.cli.build_parser()` (`klorb/src/klorb/cli.py`) makes the `-m`/`--message` flag
  optional (default `None`) and adds an `--interactive`/`--no-interactive` flag (see
  [[session-and-turns]] for its defaulting rules). `klorb.cli.main()` builds a `Session`
  and calls `run_repl(session, initial_message=args.prompt)` when the session is
  interactive, and otherwise follows the single-shot path described in
  [[openrouter-prompt-client]] via `Session.run_one_shot()`.

## Usage

```bash
klorb                          # starts the interactive REPL using the default model
klorb --model anthropic/claude-3.5-sonnet   # starts the REPL with a specific model
klorb -m "What is 2+2?"        # single-shot prompt/response, no REPL
klorb -m "What is 2+2?" --interactive   # REPL, with the message as the first turn
```

## Input history (up/down-arrow recall)

`PromptInput` keeps a per-session list of previously-submitted prompts and lets the
user recall them into the box for editing and resending via the arrow keys:

* **Recording.** On Enter, `PromptInput._record_and_submit()` appends the current
  (non-empty, non-whitespace) text to `self._history` before posting `Submitted`, so the
  entry that ends up in history is the verbatim text the user saw, not the trimmed form
  `ReplApp.on_prompt_input_submitted` dispatches to the model. Empty or whitespace-only
  submits are not recorded (mirroring the app-level guard that ignores them) and the box
  is left in place for the user to keep typing into. The recall position is reset to a
  fresh draft (`_history_index = None`) so the next up-arrow walks back from the
  just-appended entry.
* **Recall.** Up-arrow at the start of the text (`cursor_at_start_of_text`) and
  down-arrow at the end of the text (`cursor_at_end_of_text`) move the recall position
  (`_history_index`) through `self._history` and load the entry there verbatim, landing
  the cursor at the end of the recalled text so the user can append to it (readline
  behavior). That boundary check only gates *starting* a walk from a fresh, untouched
  draft (`_history_index is None`); once `_history_index` is set, further up/down presses
  keep walking regardless of where the cursor lands, since recall itself moves the cursor
  away from the boundary that triggered it. Up-arrow from a fresh draft stashes the
  draft's current text in `self._draft` and jumps to the most recent entry, then older;
  down-arrow from the most recent entry resets `_history_index` to `None` and restores
  `self._draft` rather than clearing to empty, so an in-progress draft isn't lost by
  browsing history and walking back down past it. Arrow keys anywhere else in the text
  defer to `TextArea`'s ordinary cursor movement, so the user can still navigate within a
  recalled (or any) line before a walk has started.
* **Detach.** Any text-mutating action — a printable keystroke, a deletion/editing
  binding (backspace, delete, cut, paste, undo/redo, etc.), or a bracketed paste —
  calls `_detach_from_history()`, resetting `_history_index` to `None` so the now-edited
  text is treated as a fresh draft rather than a rooted recall: the next up-arrow starts
  over from the most recent entry. Pure cursor/selection movement (the arrow keys,
  home/end, page up/down, and their shift-select variants) does not detach, so the user
  can roam a recalled line and still recall further once they reach a boundary. The
  mutation bindings are enumerated in `PromptInput._MUTATION_BINDING_KEYS` because
  `TextArea` dispatches them via `action_*` methods (triggered by the binding system)
  rather than through `_on_key`, so `_on_key` recognizes them to set the detach flag
  before the binding runs.
* **Reset on clear.** `ReplApp.clear_session()` calls `PromptInput.clear_input_history()`,
  which resets the recall position, the stashed draft, and palette/isearch state, while
  preserving the in-memory history entries so up/down-arrow recall still works after a
  session clear. A leading `>` in the recalled text is handled separately — see
  [[command-palette-from-prompt]]'s "History browsing" section.

### File-backed persistence

The input history is also persisted to disk so it survives across klorb sessions in the
same project. Each registered project (`Workspace.id`, the uuid4 key into `projects.json`)
gets one per-project directory under `$KLORB_DATA_DIR/projects/<uuid>-<basename>/`, where
`<basename>` is the last path element of the workspace root — e.g. a workspace at
`/home/aaron/src/foobar` registered as `abcd-1234` maps to
`…/projects/abcd-1234-foobar/`. An unregistered workspace (one the user declined to open
as a project, or one klorb was launched into before any bootstrap) falls back to a stable
12-hex-char hash of its canonical path so two instances opened in the *same* folder still
converge on one history file without a `projects.json` entry. The history file itself is
named `history`.

* **Format.** One previously-submitted prompt per line. Because a prompt can itself contain
  newlines (Ctrl+Enter inserts a literal newline), each entry is escaped before it's written
  (`\` → `\\`, `\n` → `\n`-literal-seq, `\r` → `\r`-literal-seq) and unescaped on the way
  back into the input box, so a recalled multi-line prompt round-trips verbatim. The trailing
  newline after the last entry is the record separator, not a blank final entry.
* **Append-only.** Every submitted message opens the file in append mode, writes its escaped
  entry plus a trailing `\n`, flushes, and closes — no caller ever rewrites the whole file.
  This is the key concurrency guarantee: multiple klorb instances editing in the same folder
  concurrently each just append their own most-recent message and never clobber one another's
  history, since each process only ever knows its own in-memory view; the file is the shared,
  append-only log.
* **Seeding.** `PromptInput.set_history_store(path)` is called once at startup (from
  `ReplApp._resolve_workspace_trust`, after the workspace is resolved) and seeds the in-memory
  `self._history` from the on-disk file so up/down-arrow recall reaches prompts submitted in
  earlier sessions. A `ReplApp` constructed without a `TrustManager` (e.g. every test) never
  sets a store, so it keeps purely in-memory recall and never touches a real `$KLORB_DATA_DIR`.
  `clear_session` does not re-seed because `clear_input_history` preserves the in-memory
  history; entries loaded at startup remain available through a session clear. The on-disk file
  is not touched by a clear — it's an append-only shared log that may have other instances
  writing to it.

### Reverse incremental search (Ctrl+R)

Pressing `Ctrl+R` enters a Readline-style reverse-incremental-search of the in-memory history
(which, after startup seeding, includes entries from prior sessions in this project). While the
search is active:

* Printable characters extend the query and re-run a newest-first, case-insensitive substring
  search, loading the match into the box.
* `Ctrl+R` again advances to the next-older match for the current query.
* `Backspace`/`Ctrl+H` shrinks the query and re-searches.
* `Enter` exits the search and submits the current match.
* `Escape` exits the search, leaving the current match in the box as an editable draft.
* Any other key (arrows, home/end, etc.) also exits the search, leaving the match in the box.

With no match, the box shows the (partial) query text, matching Readline's failing-i-search
behavior of keeping the typed search text visible.

## Out of scope

* `:q`/`/quit`/`/exit`, a leading `>` (the inline command palette, see
  [[command-palette-from-prompt]]), and the `!`-prefixed shell command mechanism described
  above are the only recognized non-prompt input.
* Thinking text is wrapped in `*...*` without escaping; a reasoning delta containing its
  own unescaped `*` could render with unbalanced/unintended emphasis. Accepted as a v1
  limitation, consistent with the response `Markdown` widget's existing unescaped handling
  of arbitrary model output.
* A `!`-prefixed command can only span one line: `on_prompt_input_submitted` only treats input
  starting with `!` as a shell command when it contains no embedded newline, so a multi-line
  shell command isn't supported (nor is escaping a literal leading `!` in an ordinary prompt).
* stdout/stderr are interleaved into one output block in arrival order, with no visual
  distinction between the two streams (unlike, say, a red-highlighted stderr).
