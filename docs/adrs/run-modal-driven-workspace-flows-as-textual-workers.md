# Run every workspace-trust flow that awaits a modal as a Textual worker, not a plain coroutine

* Date: 2026-07-05 06:00
* Question: `ReplApp._resolve_workspace_trust()`/`_bootstrap_new_workspace()` and
  `ReplApp.trust_workspace()` (see docs/specs/projects-and-trust.md) each need to push a
  `ConfirmScreen` and await the user's answer before continuing — the same
  `self.push_screen_wait(...)` call `_confirm_tool_call_limit`/`_confirm_permission_ask`
  (`klorb.tui.repl`) already use. Those two existing call sites work as plain `async def`
  methods because they're invoked via `self.call_from_thread(...)` from a worker *thread*
  (`_send_prompt`'s `@work(thread=True)`), which happens to run inside an active Textual worker
  context. `_resolve_workspace_trust()` needs to run once at startup, directly from
  `App.on_mount` — and `trust_workspace()` needs to run when the "Trust workspace" palette
  command is selected, via `PromptInput._run_palette_command`'s `self.app.call_later(...)`
  scheduling — neither of which is itself a worker. Textual's `push_screen(wait_for_dismiss=True)`
  (what `push_screen_wait` calls) explicitly raises `NoActiveWorker` outside of one
  (confirmed empirically: an earlier version of this code that called `push_screen_wait`
  directly from `on_mount`/`trust_workspace` raised exactly this at runtime). How should these
  two new call sites get a valid worker context?
* Answer: Decorate both with `@work()` (a plain, non-thread Textual worker — `thread` defaults
  to `False`) rather than leaving them as ordinary `async def` methods called directly:
  * `on_mount` stays synchronous and calls a new `@work()`-decorated
    `_run_startup_workspace_and_initial_message()`, which awaits
    `_resolve_workspace_trust()` and then submits `self._initial_message` (moved here so the
    first turn waits for the bootstrap to actually finish, rather than racing it).
  * `trust_workspace()` itself is `@work()`-decorated. Calling a `@work()`-decorated method
    starts the worker and returns a `Worker`, not a coroutine, so
    `TrustWorkspaceCommandProvider`/`SupportsTrustWorkspace.trust_workspace()` no longer need to
    be awaited by their caller (`PromptInput._run_palette_command` already tolerates a
    non-awaitable command return via `inspect.isawaitable()`).
* Reasoning: `@work()` is the one Textual-documented way to get a coroutine tracked as an
  "active worker" (`textual.worker.active_worker`, a `ContextVar`) without a real OS thread —
  exactly what `push_screen_wait` checks for via `get_current_worker()`. The alternative of
  routing these through `call_from_thread` (mirroring the two pre-existing call sites) would
  mean spinning up a throwaway thread purely to satisfy a context-var check, for work that's
  already async and belongs on the event loop — needless complexity for no benefit. Moving
  `self._initial_message`'s submission into the same worker (rather than leaving it in
  `on_mount`, racing the now-backgrounded bootstrap) also means a `-m`-supplied first prompt
  can't be dispatched against permissions that are still mid-resolution.
