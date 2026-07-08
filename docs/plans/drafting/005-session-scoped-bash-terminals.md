# Session-scoped bash terminals

Design plan for the `TODO.md` bullet: "the bash tool runs each command in its own shell that has
a shellLifetime='command' scope... Implement a shellLifetime='session' version where a shell can
be created that is persistent and reused from command to command... Also add a shellLifetime='new'
that kills the existing shell and starts a new one."

This plan extends `BashTool` (`klorb.tools.bash`, docs/specs/bash-tool-and-command-permissions.md)
rather than introducing a new tool: a persistent shell is a different lifetime for the same
"run a shell command" capability, not a different capability, so it belongs on the same schema
the model already knows.

## Scope for this iteration

* At most one live session-scoped (persistent) shell per `Session`, at a time. `TODO.md` raises
  an open question about multiple concurrent session-durable shells and how the agent would
  address a specific one — deliberately out of scope here. A single, well-known slot is enough
  to make the feature useful (an agent doing a multi-step build/debug workflow that needs `cd`
  and exported-variable state to persist) without having to design a naming/addressing scheme
  first.
* `shell_lifetime` (the existing schema field — the model already sends this on every `Bash`
  call, currently locked to the single enum value `"command"`) grows two more values:
  * `"session"` — reuse the live persistent shell if one exists; otherwise create one and use it.
  * `"new"` — kill any existing persistent shell first, then create a fresh one and use it. (The
    fresh shell then *becomes* the persistent one for subsequent `"session"` calls — `"new"` is
    "reset and use", not "use once and discard.")
  * `"command"` is unchanged: today's fresh-shell-per-call behavior, no persistence, remains the
    default/only-until-now value and this plan doesn't touch its code path.
* The user's message used the word "scope" for this concept, matching `TODO.md`'s own phrasing
  ("shellLifetime='command' scope") — this plan keeps the existing field name `shell_lifetime`
  and adds new *values* to it, rather than introducing a same-purpose second field named `scope`.

## Where persistent-shell state lives

`ToolRegistry.instantiate_tool()` builds a **fresh** `Tool` instance for every single call
(`klorb/src/klorb/tools/registry.py:74-80` — the class docstring is explicit: "a tool never
carries state over between calls"). So a `subprocess.Popen` handle cannot live on `self` inside
`BashTool` — it must live in `Session.tool_state["Bash"]`, exactly like the existing
`sandbox_warned` one-time-notice flag (`klorb/src/klorb/tools/bash.py:34-40`), which is the
established pattern for "state a tool needs to keep across calls within one session."

Concretely: a small wrapper class (recommend keeping it in `klorb/src/klorb/tools/bash.py` itself
for now — it's intimately tied to `BashTool`'s env-building and execution details, and splitting
it into its own module before it's proven to need one would be a premature abstraction), e.g.
`PersistentShell`, holding:

* the `subprocess.Popen` handle (stdin/stdout/stderr all `PIPE`, started the same way
  `_execute()` starts a one-shot shell today — same `build_bash_env()`/`--rcfile`/`-i`
  construction, same `start_new_session=True` so the shell is its own process-group leader for
  signal delivery),
* two background reader threads pumping stdout/stderr (mirroring the existing pump-thread pattern
  cited in docs/specs/bash-tool-and-command-permissions.md and `klorb/src/klorb/tui/shell.py`'s
  `UserShellCommand`), buffering lines until a per-call sentinel is seen (see "Running a command
  in a persistent shell" below),
* the last-known cwd (a plain string, updated after every command that runs to completion — see
  below), so other code can cheaply ask "where is this shell right now" without paying for a
  fresh `pwd` round-trip just to answer the question.

`session.tool_state["Bash"]["persistent_shell"]: PersistentShell | None` is the single slot this
plan's "at most one" constraint maps onto directly — creating one when none exists (or one has
died) writes it, `"new"` replaces it, and nothing else in the codebase needs to know its shape.

## Running a command in a persistent shell

Unlike the one-shot path (`bash -c "<command>"`, wait for exit, read whatever's in the output
files), a persistent shell never exits between commands, so there's no process-exit signal to
wait on. Each command needs an explicit, in-band "this command is done, here's its exit status"
marker written to the shell's own stdin, and the reader threads watch for it:

```
<command>
__klorb_ec=$?
printf '\n%s %d\n' "__KLORB_DONE_<token>__" "$__klorb_ec"
printf '\n%s\n' "__KLORB_DONE_<token>__" >&2
```

`<token>` is a fresh random token per call (e.g. `uuid4().hex`), not a fixed string — a fixed
sentinel risks colliding with a command's own real output, which is the same class of concern
`docs/specs/bash-tool-and-command-permissions.md` already flags for other constructs, just
applied to output framing instead of input classification. The stdout reader thread collects
everything before the line matching `__KLORB_DONE_<token>__ (\d+)$` as this call's `stdout`,
capturing the exit code from that line; the stderr reader thread does the same, using the bare
`__KLORB_DONE_<token>__` line (no exit code needed on that side — `$?` is already captured from
the stdout leg) as its own end marker. `__klorb_ec=$?` is captured into a variable *before* the
first `printf` runs, since the `printf` itself would otherwise clobber `$?`.

After each command, run `pwd` through the exact same mechanism (one more sentinel-delimited
round trip) and cache the result as the shell's known cwd — this is the "run `pwd` in the
terminal to also helpfully report the terminal's cwd" behavior asked for, and it's simplest to
implement as "the wrapper always knows its own current cwd" rather than something `BashTool`
re-derives per call.

**Known limitation, consistent with the rest of `BashTool`:** there is no pty here, matching the
existing one-shot path's deliberate no-controlling-terminal design (see
docs/specs/bash-tool-and-command-permissions.md's "env vars" section on `isatty()`). Interactive
programs that need a real terminal (an editor, a pager without `-F`, a password prompt) will not
work through a session-scoped terminal any more than they do through a one-shot `Bash` call today
— this isn't a new gap this plan introduces.

**Alternative considered and deferred:** a real pty (via Python's `pty` module or a
`pexpect`-style dependency) plus OSC 133 shell-integration escape sequences (the same
prompt-boundary marker convention VS Code/iTerm2/kitty use for their own shell integration) would
be a more robust way to delimit command boundaries than a printed text sentinel, and would also
make interactive programs viable. It's a materially bigger lift (new dependency or nontrivial pty
plumbing, and a `PROMPT_COMMAND`/`trap DEBUG` hook to emit the escape codes — note that
`docs/plans/ready/004-bash-permissions-and-bash-tool.md`'s rejection of `trap DEBUG` as a
*security* boundary doesn't apply here, since the agent isn't adversarial to its own terminal
feature; this would be a purely cooperative scripting use of the same bash primitive, a different
threat model, worth spelling out explicitly if this ever gets revisited so a future reader doesn't
conflate the two). Recommend leaving this as documented future work rather than building it now.

## Timeout and failure handling

* The existing `tools.bash.timeout` (`ProcessConfig.bash_timeout_seconds`) applies to a
  session-scoped command the same way it applies to a one-shot one. There's no separate
  process-group-per-command inside a persistent shell (all commands share the one shell's
  process group), so on timeout: send `SIGINT` to the whole process group first (`os.killpg`,
  matching how a real terminal's Ctrl-C is delivered to its foreground group) and give it a short
  grace period to unwind; if the sentinel still hasn't appeared, escalate to `SIGKILL` on the
  process group, which ends the persistent shell itself, not just the stuck command — there is no
  clean way to kill *only* the stuck command without a pty/job-control layer this plan isn't
  building. The tool result reports `terminal_alive=false` in that case (see "Response shape"
  below), and the model must issue `shell_lifetime="session"` again to get a fresh one; this is a
  deliberate simplification for v1, not an attempt to make a hung command recoverable in place.
* If the model's command itself ends the shell (a literal `exit`, or the shell process dying for
  any other reason), the reader thread(s) see EOF instead of a sentinel; `BashTool` reports
  whatever output was captured up to EOF, `terminal_alive=false`, and clears
  `tool_state["Bash"]["persistent_shell"]`.
* `shfmt`-based command classification and `commandRules`/`readDirs`/`writeDirs` permission
  checks (`BashTool._classify()`) run exactly the same way regardless of `shell_lifetime` — a
  session-scoped command is checked before it's ever written to the persistent shell's stdin,
  identically to how a one-shot command is checked before its `bash -c` is spawned. This plan
  doesn't change the permission-checking path at all, only what happens once a verdict is
  `"allow"`.

## Response shape

For `shell_lifetime` in `{"session", "new"}`, the existing response schema
(`command`, `exit_status`, `success`, `failure_reason`, `stdout`/`stdout_file`,
`stderr`/`stderr_file`, `runtime`) gains two additional fields, populated from the same call:

* `terminal_alive: bool` — whether the persistent shell is still usable after this command.
* `terminal_cwd: str | None` — the shell's cwd (via the `pwd` round-trip described above), or
  `null` if `terminal_alive` is `false`.

This is per-call, dynamic result data, not part of `BashTool.description()` — the tool
description is sent as part of `tool_definitions()` on every turn and is the kind of content a
caller may want to stay a stable, cacheable prefix (per direct guidance from the user drafting
this plan), so nothing about *this call's* outcome belongs there. `description()` only needs one
small, static addition: documenting that `shell_lifetime` now accepts `"session"`/`"new"` and
what each means, exactly like its existing static description of `"command"` today.

## Informing the agent when a live terminal outlives the turn that created it

The response fields above cover "the model just called `Bash` again and wants to know where
things stand." They don't cover the case the user specifically flagged: the model created a
session-scoped terminal in an earlier turn, doesn't call `Bash` at all in a later turn (working
on something else), and would otherwise have no way to remember the terminal is still open and
consuming state (a `cd` from three turns ago, an exported variable, a background job) — or even
that it exists at all, if the fact scrolled out of a compacted context.

**Existing prior art to build on:** PR #25 (`kimballa/klorb#25`, open/unmerged as of this
writing — branch `claude/permission-framework-interjection-r0p33z`) implements exactly the
`TODO.md` backlog item about telling the model when `permissionFramework` changes mid-conversation,
via a `<SystemInterjection subject="...">...</SystemInterjection>`-wrapped string
(`klorb.session._wrap_system_interjection()`) prepended onto the next turn's prompt inside
`Session.send_turn()`. That mechanism is **edge-triggered and one-shot**: `set_permission_
framework()` sets a single `_pending_permission_framework_interjection: str | None` field,
`send_turn()` prepends it and clears it, so it fires exactly once, for the very next turn only.

A live session-scoped terminal is a **level-triggered, standing** condition, not a one-time
event — it should keep being mentioned for as long as it's actually alive, however many turns
pass without the model touching it, not just once right after it was created. Trying to squeeze
this into PR #25's single dedicated field (or duplicating a second copy of the same "set a flag,
prepend once, clear it" logic) doesn't fit that shape and shouldn't be forced into it. This plan
proposes adding a second, complementary piece of machinery to `Session`, reusing
`_wrap_system_interjection()` as-is (it's already generic over `subject`) but not touching
PR #25's one-shot field or logic at all:

* `Session.register_standing_interjection(subject: str, provider: Callable[[], str | None]) -> None`
  stores `provider` in a new `self._standing_interjection_providers: dict[str, Callable[[],
  str | None]]`, keyed by `subject` (so re-registering under the same key is a harmless
  overwrite, not an accumulating list — see below for why that matters).
* At the top of `send_turn()`, alongside the existing one-shot-interjection check, call every
  registered provider; each returns either a message string (wrapped in
  `<SystemInterjection subject="{subject}">` via the existing helper) or `None` (nothing to say
  right now — the provider itself is responsible for deciding whether its condition still
  holds). Concatenate whatever comes back, in a fixed order, ahead of the prompt.
* `BashTool` registers (or re-registers — every call constructs a fresh `BashTool`, so this must
  be idempotent) a `"SessionTerminal"` provider whenever it creates or reuses a persistent shell:
  a small closure/bound method reading the `PersistentShell` wrapper's live/dead state and its
  last-known cwd, returning something like `"You have a live session-scoped Bash terminal open
  (cwd: {cwd}). Use shell_lifetime=\"session\" to keep using it, or shell_lifetime=\"new\" to
  close it and start fresh."` when alive, or `None` once the shell has died (at which point the
  provider naturally stops firing on its own — no separate unregister call is needed; a dead
  shell's provider just returns `None` forever after, which costs nothing).

Why not have `Session` read `tool_state["Bash"]` directly instead of adding a registration API?
`Session.tool_state`'s own docstring (`klorb/src/klorb/session.py:319-327`) currently documents
it as "never read or written by `Session` itself" — deliberately opaque, tool-private bookkeeping.
Reaching into `tool_state["Bash"]["persistent_shell"]` from `session.py` would both break that
documented invariant and require `session.py` to know the exact private shape of one specific
tool's state. A `Callable[[], str | None]` handed to `Session` explicitly keeps `Session` generic
(it doesn't know or care that `"SessionTerminal"` means "a bash shell" — it just calls providers
and prepends non-`None` results) and avoids `klorb/src/klorb/session.py` importing anything from
`klorb.tools.bash` (which would be circular, since `bash.py` already imports `SessionConfig` from
`klorb.session`).

**Open question for whoever implements this:** repeating the same reminder on every single turn
while a terminal stays alive could get noisy in a long session. Recommend shipping the simple
"always include it while alive" version first (least surprising, no extra bookkeeping about
"have I already reminded the model recently"), and only add suppression/throttling if it proves
annoying in practice.

## Cleanup and lifecycle

* Killed and replaced by `shell_lifetime="new"` if one already existed (`SIGKILL` the process
  group, close the pipes, then spawn fresh — same as the timeout-escalation path above, just
  triggered by the model instead of a hung command).
* **`/clear` currently orphans this kind of state.** `ReplApp.clear_session()`
  (`klorb/src/klorb/tui/repl.py:1549-1575`) replaces `self._session` with an entirely new
  `Session` object and never calls anything on the old one — there is no `Session.close()`/
  teardown method anywhere in the codebase today. Without a fix, `/clear` while a session-scoped
  terminal is alive would leak the underlying bash process (and its reader threads) for the rest
  of the klorb process's lifetime. This plan needs to add:
  1. A small `Session` teardown method (e.g. `Session.close()`) that kills any live
     `tool_state["Bash"]["persistent_shell"]` process, if present.
  2. A call to it from `clear_session()` before constructing the replacement `Session`.
  3. A process-wide `atexit` fallback (mirroring the existing per-invocation temp-dir cleanup in
     `klorb/src/klorb/tools/bash.py:349-352`, but tracking whatever persistent-shell PID is
     currently live) so a klorb process exiting normally, via `^C`, or via an uncaught exception
     — none of which necessarily go through `clear_session()` — never leaves a bash process
     behind.
* No locking is needed for v1: `Session._run_tool_calls()` dispatches every tool call in a turn's
  round sequentially, in a single `for` loop (`klorb/src/klorb/session.py:819-884`), so two calls
  can never race to write to the same persistent shell's stdin concurrently within one `Session`.
  This would need revisiting if/when `TODO.md`'s forward-looking "parallel tool calls" idea is
  ever built — explicitly out of scope here, matching the "at most one terminal" constraint this
  plan was asked to assume.

## Configuration

* `BashTool.parameters()`'s `shell_lifetime` JSON-schema `enum` grows from `["command"]` to
  `["command", "session", "new"]`. No other schema changes.
* No new on-disk config keys are required for v1. A separate `tools.bash.sessionShellTimeout`
  (different timeout for persistent-shell commands than one-shot ones) is a plausible future
  addition but not needed to ship this — reuse `tools.bash.timeout` for both, and revisit only if
  real usage shows persistent-shell commands need different timeout behavior.
* `pwd` has been added to `sessionDefaults.commandRules.allow` in
  `klorb/src/klorb/resources/default-config.json` (bare `["pwd"]`, matching the existing
  no-argument entries like `["sort"]`/`["uniq"]`) — done as part of drafting this plan, not
  deferred to its implementation, since it's a small, independent, low-risk default-allowlist
  change useful regardless of when this feature ships (the model can already usefully run `pwd`
  on its own today, via the one-shot path). If `pwd -L`/`pwd -P` turn out to matter in practice,
  broadening to `["pwd", "**"]` is a one-line follow-up.
* No `schema: {name, version}` envelope is needed anywhere in this feature — `Session.tool_state`
  is explicitly in-memory-only (never persisted to disk, per its own docstring), and a live
  `subprocess.Popen` handle couldn't be meaningfully persisted across process restarts even if
  something tried. `docs/specs/persisted-json-schema-versioning.md`'s convention doesn't apply.

## Testing

* Regression coverage: every existing `shell_lifetime="command"` test in `klorb/tests/test_bash.py`
  continues to pass unmodified — this plan doesn't touch that code path.
* `cd`/env persistence: two separate `apply()` calls with `shell_lifetime="session"` (one `cd
  /some/dir`, one plain `pwd`) should show the second call's `terminal_cwd`/output reflecting the
  first call's `cd`.
* `shell_lifetime="new"` kills a prior persistent shell: start one, capture its `$$`, request
  `"new"`, confirm the following `echo $$` reports a different pid than the first.
* Timeout kills the whole persistent shell, not just the stuck command: a session-scoped `sleep`
  well past `tools.bash.timeout` should come back with `terminal_alive=false`, and a following
  `shell_lifetime="session"` call should transparently create a brand new shell rather than
  erroring.
* `exit` run inside a persistent shell is reported as `terminal_alive=false` and clears
  `tool_state["Bash"]["persistent_shell"]`.
* `Session.close()` (or whatever the teardown method ends up named) actually kills a live
  persistent shell's process — assert the pid is no longer alive afterward.
* `clear_session()` in the REPL invokes that teardown before swapping in the new `Session`.
* The `"SessionTerminal"` standing interjection: appears (wrapped in the expected
  `<SystemInterjection subject="SessionTerminal">` tag) on a turn dispatched after a prior turn
  left a live persistent shell behind, with no `Bash` call in the new turn; does not appear once
  the shell has died; coexists correctly with PR #25's independent one-shot permission-framework
  interjection if both happen to be pending on the same turn (order is fixed and deterministic,
  whatever order implementation picks).

## Sequencing note

This plan builds directly on PR #25's `_wrap_system_interjection()` helper and
`<SystemInterjection subject="...">` tag convention, but doesn't depend on PR #25's one-shot
pending-field logic being merged in any particular shape — the new standing-interjection
machinery proposed above is additive and independent of it. Implementing this plan is easiest
once PR #25 has actually landed on `main` (so `_wrap_system_interjection()` exists to reuse
rather than needing to be introduced by this plan instead), but is not blocked on it in principle.

## ADRs to write once implementation begins

* Sentinel-token-over-pipes (not a pty) as the v1 mechanism for delimiting command boundaries in
  a persistent shell, and pty/OSC-133 as documented future work rather than a v1 requirement.
* A "standing" (recomputed-every-turn, provider-callback-based) interjection mechanism as a
  deliberate complement to PR #25's "one-shot, edge-triggered" interjection mechanism, rather
  than forcing one shape to cover both — including why `Session` calls an opaque registered
  callback instead of reading `tool_state` directly.
* Capping session-scoped terminals at exactly one per `Session` for v1, deferring the
  multiple-concurrent-shells addressing question `TODO.md` raises.

## Out of scope / future work

* Multiple concurrent session-scoped terminals and how the model would address a specific one
  (`TODO.md`'s open question).
* A real pty and OSC-133-style shell-integration escape codes for more robust command-boundary
  detection and support for interactive programs.
* Persisting a session-scoped terminal's existence across `/clear` or across separate klorb
  process invocations — a live OS process can't meaningfully survive either.
* `bwrap` sandboxing of a persistent shell — the one-shot path's own sandboxing
  (`klorb.sandbox.build_bwrap_argv`) is already a stub (see
  docs/plans/ready/004-bash-permissions-and-bash-tool.md); a persistent shell inherits the same
  unsandboxed status until that lands.
* Throttling/deduplicating the standing "you still have a live terminal" reminder if it proves
  noisy over long sessions with many turns.
