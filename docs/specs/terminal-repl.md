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

* `klorb.tui.repl` (`klorb/src/klorb/tui/repl.py`) defines `ReplApp`, a `textual.app.App`
  subclass, and `run_repl(session, initial_message, session_log_enabled)`, a thin function
  that constructs and runs it. `ReplApp` takes a [[session-and-turns]] `Session`
  (constructing a default one if none is given) rather than a raw `ApiProvider`/model pair,
  so the REPL sends every turn through the same `Session.send_turn()` path a one-shot
  prompt uses. `session_log_enabled` records whether `cli.main()` turned on per-session
  logging for this invocation, so `/clear` (below) knows whether to roll the log file over.
* `ReplApp.compose()` lays out four widgets/regions top-to-bottom: a `Header` showing the
  app title and the active model as its subtitle, a `VerticalScroll` (id `history`) that
  holds the conversation so far, an `Input` (id `prompt-input`) for typing the next prompt,
  and a `Horizontal` (id `status-row`) docked to the bottom of the screen that holds the
  `Footer` (key bindings) side by side with a `Static` token-tally widget (id `status-bar`)
  in the same row — so the tally reads like one more item alongside `^q Quit`/`^p palette`
  rather than a separate line. The history container is styled `height: 1fr` so it fills
  all available vertical space above the input box, which is why the history scrolls "up"
  as content is added while the input box stays pinned to the bottom of the screen.
* `Footer`'s own `dock: bottom` CSS rule is overridden to `dock: none; width: 1fr` when
  nested inside `#status-row`, since Textual resolves a docked widget's position against
  its immediate parent: left docked, `Footer` would claim the entire row's height for
  itself (its dock arrangement is computed independently of siblings) and collide with
  `#status-bar` instead of sharing the row. With the dock removed, `Footer` behaves as a
  normal flex child that takes up the remaining space next to the fixed-width tally.
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
  recomputes and redraws the bar on mount, after switching models, after `/clear`, and at
  the end of every turn (success or error).
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
  CSS, since a plain `Static` has none) whose content is the accumulated text run through
  `klorb.tui.repl._italicized()`; later chunks re-wrap the growing accumulated text the
  same way and call `.update()` on that same `Static`. `_italicized()` escapes any literal
  `[`/`]` in the text (via `rich.markup.escape`, so reasoning text containing brackets
  can't be misread as markup) and wraps the result in `[italic]...[/italic]` Rich console
  markup rather than Markdown's `*...*` emphasis syntax — deliberately, since reasoning
  text commonly spans multiple paragraphs and Markdown emphasis doesn't apply across
  blank-line-separated blocks the way Rich's per-line style markup does (a `Markdown`
  widget was tried first and silently failed to italicize multi-paragraph reasoning; see
  [[render-thinking-body-as-rich-markup-not-markdown]]).
  There's no non-streaming fallback for the thinking block (unlike the response): if
  nothing ever streamed as reasoning, no thinking block is shown, since there'd be no text
  to show.
* Pressing Escape while a response is streaming in aborts it: `ReplApp` creates a fresh
  `threading.Event` per submitted prompt and passes it as `Session.send_turn()`'s
  `cancel_event`, and Escape (bound to `action_abort_response`, shown in the footer only
  while a turn is in flight via `check_action`) sets it. `_send_prompt`'s worker thread
  catches the `ResponseAborted` this raises, tears down every widget mounted for that turn
  (the echoed prompt, and any partial response/thinking widgets), and writes the original
  prompt text back into the now-re-enabled input box so the user can edit and resend it.
  `Session` has already discarded the turn from `self.messages` by this point — it's as if
  it never happened, not a new errored turn — see
  [[escape-aborts-streaming-turn-and-discards-it-from-history]].
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
* `Ctrl+P`'s command palette also includes `ThinkingCommandProvider`
  (`klorb/src/klorb/tui/thinking_commands.py`), listing `"Enable thinking"`, `"Disable
  thinking"`, and a single `"Set thinking effort"` command (rather than one palette entry
  per `ThinkingEffort` level, which cluttered the palette). Selecting `"Enable
  thinking"`/`"Disable thinking"` calls `ReplApp.set_thinking_enabled(bool)` directly, which
  mutates `Session.config.thinking_enabled` (same pattern as `select_model()`) and shows a
  toast confirming the change. Selecting `"Set thinking effort"` instead reads the current
  level via the new `ReplApp.get_thinking_effort()` getter and pushes `ThinkingEffortScreen`,
  a `ModalScreen` with a `"Thinking effort level:"` header `Static` above the three
  `ThinkingEffort` levels (`"low"`/`"medium"`/`"high"`) listed vertically in an `OptionList`,
  with the currently-active level's entry suffixed with `" *"`; the up/down arrow keys move
  the selection and Enter confirms it (`OptionList`'s built-in bindings), calling
  `ReplApp.set_thinking_effort(level)` and dismissing the modal, while Escape dismisses
  without changing anything. `"Enable thinking"`/`"Disable thinking"` remain an always-on/off
  pair, mirroring how `ModelCommandProvider` always lists every model rather than showing
  dynamic toggle-state labels.
* Typing `/clear` and pressing enter, instead of submitting a prompt, replaces the active
  `Session` with a new one: same `SessionConfig` (model carries over) and the same
  `provider`/`model_registry` instances (via `Session`'s read-only properties, so the
  OpenAI client and model discovery aren't rebuilt), but a fresh `generate_session_id()`,
  a fresh [[tool-framework]] `ToolRegistry` built from `ReplApp._process_config` and the new
  session's `SessionConfig` (unlike `provider`/`model_registry`, not reused — see
  [the fresh-instance-per-call ADR](../adrs/tool-registry-instantiates-a-fresh-tool-per-call.md)),
  and an empty message history. This runs synchronously in `on_input_submitted` — no
  worker thread, no disabling the input box, since no model call is involved. The visible
  history's children are removed, and if `session_log_enabled` is `True`,
  `configure_logging()` is called again with a new `session_log_path()` for the new
  session id (relying on `configure_logging`'s `force=True` behavior to safely repoint the
  root logger's file handler mid-process). See
  [[clear-command-starts-a-new-session-and-log-file]].
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
  output); later lines `.update()` the same widget with the growing accumulated text, escaped
  via `rich.markup.escape` so literal `[`/`]` in the output (e.g. `[INFO]` log tags) can't be
  misread as Rich console markup. Only one shell command can be in flight at a time for a
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
* `klorb.cli.build_parser()` (`klorb/src/klorb/cli.py`) makes the `-m`/`--message` flag
  optional (default `None`) and adds an `--interactive`/`--no-interactive` flag (see
  [[session-and-turns]] for its defaulting rules). `klorb.cli.main()` builds a `Session`
  and calls `run_repl(session, initial_message=args.prompt)` when the session is
  interactive, and otherwise follows the single-shot path described in
  [[openrouter-prompt-client]] via `Session.run_one_shot()`.

## Usage

```
klorb                          # starts the interactive REPL using the default model
klorb --model anthropic/claude-3.5-sonnet   # starts the REPL with a specific model
klorb -m "What is 2+2?"        # single-shot prompt/response, no REPL
klorb -m "What is 2+2?" --interactive   # REPL, with the message as the first turn
```

## Out of scope

* `/clear` and `:q`/`/quit`/`/exit` are the only recognized non-prompt input, alongside the
  `!`-prefixed shell command mechanism described above. Input history (up-arrow to recall a
  previous prompt) is not implemented yet.
* Tool call activity itself isn't rendered in the visible history — no bubble shows "called
  ReadFile(...)" or its result mid-turn; the user sees only the eventually-final response (or
  `ToolCallLimitScreen`, if a tool-call safety cap is reached along the way).
* Thinking text is wrapped in `*...*` without escaping; a reasoning delta containing its
  own unescaped `*` could render with unbalanced/unintended emphasis. Accepted as a v1
  limitation, consistent with the response `Markdown` widget's existing unescaped handling
  of arbitrary model output.
* A `!`-prefixed command can only span one line: `on_prompt_input_submitted` only treats input
  starting with `!` as a shell command when it contains no embedded newline, so a multi-line
  shell command isn't supported (nor is escaping a literal leading `!` in an ordinary prompt).
* stdout/stderr are interleaved into one output block in arrival order, with no visual
  distinction between the two streams (unlike, say, a red-highlighted stderr).
