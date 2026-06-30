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
  subclass, and `run_repl(session, initial_message)`, a thin function that constructs and
  runs it. `ReplApp` takes a [[session-and-turns]] `Session` (constructing a default one if
  none is given) rather than a raw `ApiProvider`/model pair, so the REPL sends every turn
  through the same `Session.send_turn()` path a one-shot prompt uses.
* `ReplApp.compose()` lays out four widgets top-to-bottom: a `Header` showing the app title
  and the active model as its subtitle, a `VerticalScroll` (id `history`) that holds the
  conversation so far, an `Input` (id `prompt-input`) for typing the next prompt, and a
  `Footer` showing key bindings. The history container is styled `height: 1fr` so it fills
  all available vertical space above the input box, which is why the history scrolls "up"
  as content is added while the input box stays pinned to the bottom of the screen.
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
  On success, `_submit_prompt`'s worker mounts a `Markdown` widget with the model's
  response (rendered with Textual's built-in markdown renderer, since model output is
  frequently markdown); on failure, it mounts a `Static` widget with the exception message
  (styled via the `.error` CSS class). Either way, the history is scrolled to the end
  again, and the input box is re-enabled and refocused.
* `Ctrl+C` and `Ctrl+Q` quit the REPL. `Ctrl+P` opens Textual's command palette, which
  includes `ModelCommandProvider` for switching the active model — see
  [[model-framework]]. Selecting a model updates `Session.config.model` directly.
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

* Multi-turn conversation history is not sent back to the model — each submitted prompt is
  still a single, independent `send_prompt` call with no prior turns included in the
  request. The REPL only gives the *visual* appearance of a conversation.
* Tool/function calling, slash commands, input history (up-arrow to recall a previous
  prompt), and streaming token-by-token responses are not implemented yet.
