# Bash tool cancellation reads Session.active_cancel_event, not a constructor parameter

* **Date:** 2026-07-16
* **Question:** A Ctrl+C/Escape interrupt sets a `threading.Event` (`TurnEventHandlers.
  cancel_event`) that `Session._dispatch_turn`/`_run_tool_calls` only check at tool-call/round
  boundaries — so a long-running `BashTool` call blocks until it finishes on its own, ignoring
  the interrupt until the *next* boundary. How should the currently-executing tool call reach
  the same cancellation signal so it can send `SIGINT` to its own child process immediately?
* **Answer:** Add `Session.active_cancel_event: threading.Event | None`, set to
  `callbacks.cancel_event` at the top of `_dispatch_turn` and cleared in its `finally`. A `Tool`
  reads it via `self.context.session.active_cancel_event` (`ToolSetupContext.session` already
  exists for this kind of per-session, runtime-only state). Do not thread `cancel_event` through
  `ToolRegistry.instantiate_tool()`/`ToolSetupContext`'s constructor.
* **Reasoning:**
  * `Tool.apply()` runs synchronously, on the same worker thread as `_dispatch_turn`, for the
    entire lifetime of one tool call. There is exactly one turn (and therefore one
    `cancel_event`) in flight at a time per `Session` — `ReplApp._turn_in_flight` already
    enforces that no second turn can start concurrently — so a single mutable attribute on
    `Session` has no race to guard against; nothing else runs on that thread while the tool call
    is executing.
  * Threading `cancel_event` through `ToolSetupContext`'s constructor would mean every call to
    `ToolRegistry.instantiate_tool()`/`._context()` — including the ones `ToolRegistry.
    _discover_tools()` makes at startup with no turn in flight at all — would need a
    `cancel_event` parameter that's `None` almost everywhere it's threaded through. `Session`
    already owns the one place a turn's lifetime is tracked; exposing the same event as a
    session attribute needs no change to `ToolRegistry`, `ToolSetupContext`, or any other tool's
    constructor.
  * The alternative — polling `callbacks.cancel_event` from inside `Session._run_tool_calls` on a
    background thread and calling into the tool to cancel it — would need every cancellable tool
    to expose some kind of `cancel()` method and a way for `Session` to reach the *specific*,
    currently-running instance, which doesn't otherwise exist (a fresh `Tool` instance is built
    per call and discarded). Reading a shared, already-in-scope event from inside the tool's own
    blocking wait loop is far simpler and needs no new protocol between `Session` and `Tool`.
  * `BashTool` uses this for both its one-shot (`_execute`) and persistent-shell
    (`PersistentShell._run_raw`) execution paths, polling the event at the same cadence
    (`_CANCEL_POLL_SECONDS`) the `!`-prefixed direct-shell feature (`klorb.tui.shell.
    UserShellCommand`) already uses for its own, separately-threaded `cancel_event` — see
    docs/specs/bash-tool-and-command-permissions.md's "Cancellation" section.
