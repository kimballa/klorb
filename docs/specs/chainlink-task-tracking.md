# Chainlink task tracking

## Summary

A session's agent tracks its own fine-grained work as `chainlink` issues instead of (or ahead
of) prose planning: `TodoList`/`TodoNext`/`TodoCreate`/`TodoUpdate` (category `TASKS`) shell out
to the `chainlink` CLI, scoping every operation to one root session's issues via a `label`
(`Session.get_chainlink_label()`, always `Session.root_id`). State survives context compaction
and a handoff between klorb sessions through chainlink's own comments and dependency ("blocks"/
"blocked by") graph — not through chainlink's own Session/handoff-note/breadcrumb machinery,
which klorb deliberately never uses; see docs/adrs/chainlink-issues-not-chainlink-sessions-for-
continuity.md.

The `TASKS` tool category is offered only when the `chainlink` binary can be found at all
(`klorb.tools.tasks.common.chainlink_available`); otherwise it's silently absent from
`ToolRegistry.discover_tools`'s result, exactly like any other tool category would be if its
prerequisites weren't met — there is no config flag to turn it off separately.

## The `chainlink` binary

`chainlink` (crate `chainlink-tracker`) is a lean issue tracker CLI backed by one SQLite file
per workspace (`.chainlink/issues.db`), installed via `cargo install chainlink-tracker` by
`klorb/Makefile`'s `install_chainlink` target (wired into `install_deps`/`install_dev_deps`, and
so into `make cloud_setup`). `klorb.tools.tasks.common._discover_binary()` looks it up via
`shutil.which("chainlink")` (`PATH`) first, then `$VIRTUAL_ENV/bin/chainlink` if a Python
virtualenv is active, then falls back to `$HOME/.cargo/bin/chainlink` — a `cargo install` binary
lands there whether or not `~/.cargo/bin` is on `PATH`.

Every `chainlink` invocation runs with `cwd` set to the workspace root and
`RUST_BACKTRACE=0` in its environment — `RUST_BACKTRACE=1` (as many shells default to) makes
chainlink print a full Rust backtrace after its own one-line `Error: ...` message, which would
otherwise bury the message `ChainlinkClient` wants to surface.

### Command shapes, as actually observed

Only `issue list`/`issue show`/`issue search` (and `session status`, unused here) actually honor
`--json`; every mutating subcommand (`create`/`update`/`close`/`reopen`/`block`/`unblock`/
`comment`/`close-all`) prints a plain-text confirmation line regardless of it. `ChainlinkClient.
_run()` passes `--json` unconditionally on every call anyway (harmless where it's ignored,
verified against the installed binary) plus `--quiet` when the caller asks for it (every mutating
call) — one consistent command shape, rather than each method deciding its own flags. Where a
value is actually needed from a `--quiet` call (`create`'s new id), it's parsed from the
bare-value stdout that mode produces, not JSON that was never there. `issue block`, `issue create
-w`, `issue close`/`close-all` (see "Setup", below, for `--no-changelog`), and every other
mutation return no payload `ChainlinkClient` needs, so their stdout is discarded once the exit
code confirms success.

`issue show <id>`'s `blocked_by` list is **not** pruned as a blocker closes — a closed blocker
still appears in it. `open_blocker_count()` computes "blockers still in the way" by intersecting
`blocked_by` against the set of ids `fetch_and_sort_issues()` already knows are open for this
label, rather than trusting `blocked_by`'s raw length. `chainlink issue ready`/`blocked`/`next`
do track this correctly on their own, but none of them accept `--label` or support `--json`
(confirmed against the installed binary's own `--help` and output), so they're unusable for
klorb's per-session label scoping and are not used at all; `TodoList`/`TodoNext` compute
readiness and sort order themselves instead (`klorb.tools.tasks.common.issue_sort_key`).

## Setup

`ChainlinkClient.__init__` runs `_ensure_setup()` on every construction, which is a cheap
`(workspace_root / ".chainlink" / "issues.db").exists()` check that does nothing once that file
exists. The first time it doesn't, it runs `chainlink --json init` and then:

* **Prunes chainlink's own Claude-Code-hooks/MCP scaffold.** In addition to `.chainlink/`
  itself, `chainlink init` unconditionally plants `.claude/settings.json`, `.claude/hooks/*.py`,
  `.claude/mcp/safe-fetch-server.py`, and `.mcp.json` at the workspace root — a full
  PreToolUse/PostToolUse/UserPromptSubmit/SessionStart hooks harness and MCP server registration
  built for a *real* Claude Code session, unrelated to klorb's own use of chainlink purely as a
  task-tracking backend. (`chainlink init` does skip `.claude/settings.json` specifically if one
  already exists — verified against the installed binary — but plants everything else
  regardless, and a workspace with no pre-existing Claude Code setup, the common case for a
  klorb user, gets the full scaffold with no way to opt out via a flag.) `_ensure_setup()`
  snapshots which of `.claude/settings.json`, `.claude/hooks/`, `.claude/mcp/`, and `.mcp.json`
  already existed *before* calling `init`, then deletes only the ones that didn't — never a path
  that was already there — and removes `.claude/` itself too if `init` created it and pruning
  left it empty.
* **Ensures `.chainlink/` is gitignored.** Appends a `.chainlink/` line to the workspace's
  top-level `.gitignore` (creating the file if it doesn't exist) unless the existing rules
  already cover it — checked with `pathspec.GitIgnoreSpec` (the same gitignore-matching library
  `klorb.tools.util.gitignore` uses), so a broader existing rule (e.g. a wildcard dotdir pattern)
  is correctly recognized rather than only an exact-line match. `issues.db` has no merge-conflict
  resolution story — ids are sequential integers chainlink assigns itself, with nothing to
  reconcile across branches — so it must never be committed. (chainlink's own `init` already
  writes a `.chainlink/.gitignore` covering machine-local sub-files like `rules.local/`/`.cache/`/
  `agent.json` *within* `.chainlink/`, but that file does not cover `.chainlink/` itself or
  `issues.db`, so the workspace's own top-level `.gitignore` still needs the entry.)

There is no separate "run this before the first user message" hook wired into `cli.py`/the TUI's
several `Session`-construction call sites: every `Tool` in the `TASKS` category constructs a
fresh `ChainlinkClient` in its own `apply()` (see "Tools", below), so setup happens lazily,
exactly once, on whichever `Todo*` tool call happens first — functionally equivalent to running
it eagerly at session start, without threading a new call through five separate places.

## `ChainlinkClient`

`klorb.tools.tasks.common.ChainlinkClient` is the single place all four `Tool`s shell out
through — binary discovery, the `--json`/`--quiet` command shape, and lock-contention retry
live here once, not duplicated per tool. Constructed fresh by each tool's `apply()` from its own
`ToolSetupContext`, the same per-call-fresh pattern `Tool` itself follows
(`ToolRegistry.instantiate_tool`); never cached or shared across calls. Every subprocess
invocation, with no exception, goes through the one `_run()` method — `_ensure_setup()`'s own
`chainlink init` call included — so there is exactly one place that builds a command line,
retries, and raises.

`chainlink` shares one SQLite file across concurrent sessions/subagents working in the same
workspace, so a locked-database error (`"database is locked"` in `stderr`, case-insensitive) is
retried: up to 4 attempts total, exponential backoff starting at 0.25s and doubling each retry,
plus uniform random jitter in `[-0.025s, 0.025s]` added to each wait. This retry sleeps on
whichever thread called `_run()`, which is safe only because every `Tool.apply()` call already
runs off klorb's main thread — the TUI dispatches a whole turn, tool calls included, through a
`@work(thread=True)` worker (`klorb.tui.mixins.prompt_submission`). Any other failure (or a
lock-contention failure on the final attempt) raises `ChainlinkError` naming the full command
line, the working directory, the exit code, and `stderr`'s first non-blank line — enough to
reproduce the failure by hand.

`ChainlinkClient.__init__` requires a real `Session` on its `ToolSetupContext` (raises
`ValueError` otherwise) — `label` is always `session.get_chainlink_label()` (see "Session
state", below). It also registers this session's close-time cleanup (`Session.
register_teardown("ChainlinkClient", ...)`), which runs `chainlink issue close-all --label
<label> --no-changelog` when the session ends (`atexit`, `/clear`, etc.) — see docs/adrs/
chainlink-issues-not-chainlink-sessions-for-continuity.md for why klorb uses chainlink this
ephemerally today, with no "resume a prior session's leftover work" capability yet.

`create_issue`/`update_issue` both call `validate_priority()` before shelling out at all,
raising `ValueError` for anything outside `PRIORITY_ORDER`'s keys — the JSON schema each
`Tool.parameters()` declares already restricts a well-behaved model to a valid value, but this
doesn't trust that up front.

`close_issue`/`close_all` always pass `--no-changelog`. chainlink's default `issue close`
behavior writes an entry to a `CHANGELOG.md` at the workspace root — unrelated to klorb's
ephemeral, per-session task tracking, and a source-file mutation that would otherwise happen
completely outside klorb's own permission-gated write tools (`EditFile`/`CreateFile`/
`ReplaceAll`) every time an agent closes a todo item.

## Tools

All four tools live in `klorb.tools.tasks`, category `"TASKS"`:

* **`TodoList`** (read-only) — lists issues under this session's label via
  `fetch_and_sort_issues()`: `chainlink issue list --label <label> --status <open|all>` for the
  candidate id set, enriched per-id with `chainlink issue show <id>` (needed for `blocked_by`,
  which `list` doesn't return), sorted by `issue_sort_key()` — open before closed, fewest open
  blockers first, highest priority first, then lowest id first. `ids`, if given as a single id,
  short-circuits straight to `issue show`; given as several, narrows the sorted result down to
  just those. `include_closed` includes closed issues (`--status all`), still sorted after every
  open one.
* **`TodoNext`** — first checks whether a current task is already tracked
  (`Session.cur_chainlink_task_id`) and, if that issue still resolves and is still open, returns
  *that same issue again* (does not pick a new one) with an extra `message` field telling the
  model to finish and close it first — `TodoNext` never silently abandons a task the model
  hasn't closed. Otherwise runs the `fetch_and_sort_issues(include_closed=False)` pipeline and
  picks the first issue (in sort order) with zero open blockers, via
  `Session.set_chainlink_task()`. Three outcomes overall: `work_exists=False,
  project_complete=True, task=None` (no open issues at all — done); `work_exists=True,
  project_complete=False, task=None` (open issues remain but every one is still blocked —
  nothing actionable right now); or the picked (or re-returned) issue. Registers a standing
  `<SystemInterjection subject="ChainlinkCurrentTask">` (see "Turn interjection", below) whenever
  it returns a task. `is_read_only()` is `False` despite only reading from chainlink — it mutates
  session state and registers a standing interjection as a side effect.
* **`TodoCreate`** — validates `priority` and (if `blocks_current_issue=true`) that a current
  task actually exists *before* creating anything, then `chainlink issue create --priority
  PRIORITY --label LABEL [-d DESCRIPTION] TITLE` (`--quiet`, parsing the new id from its
  bare-value stdout), then `chainlink issue block` once per `blocked_by` id (the new issue is
  blocked by each), once for `blocks_current_issue=true` (the current task is blocked by the new
  issue), and once per `blocks_issues` id (that issue is blocked by the new one). If any of
  those `block()` calls fails partway through, the new issue is closed with a comment explaining
  why rather than left behind half-configured, and the original error is re-raised — creation is
  best-effort atomic. Returns the new issue's full `issue show` detail.
* **`TodoUpdate`** — dispatches whichever of its arguments are present to the matching
  chainlink subcommand against `id`: `new_title`/`new_description`/`new_priority` →
  `issue update`; `depends_on`/`drop_dependency` → `issue block`/`issue unblock` per id;
  `add_comment` → `issue comment`; `close`/`reopen` → `issue close --no-changelog`/`issue
  reopen`, applied last (so a comment added in the same call lands before the issue closes).
  Returns the updated issue's full `issue show` detail.

None of the four apply any `ProcessConfig`-driven permission gate (unlike, say,
`tools.memory.readPermission`) — chainlink's SQLite file lives under the workspace's own
`.chainlink/` and is never read/written outside it, so there's no filesystem-boundary decision
to make the way there is for `ReadFile`/memory tools.

## Session state

`Session.root_id: str` (default: the session's own `id`) names the root session a `Session`
descends from — itself, for every session today, since klorb has no subagent-spawning mechanism
yet (`klorb.role.Role.repertoire`). `get_chainlink_label()` returns it, and is the only way
`ChainlinkClient` learns what label to scope issues by — it never reads `session.id` directly.
The indirection exists so that a future subagent's `Session` can pass its parent's `root_id`
through its own constructor and end up sharing one label with the rest of that task tree, rather
than every subagent scoping issues to its own, narrower `id`.

`Session.cur_chainlink_task_id: int | None` (default `None`) tracks the issue id `TodoNext` most
recently picked (or re-returned). Written only through `Session.set_chainlink_task(task_id)` —
`TodoNext` is the only caller — never assigned directly by a `Tool`; read directly (a plain
public attribute, like `active_cancel_event`) by `TodoCreate`'s `blocks_current_issue` handling
and the standing interjection provider.

Both round-trip through `last-session.json`
(`klorb.workspace.last_session.LastSessionState.root_id`/`cur_chainlink_task_id`, additive
optional fields — no `schema.version` bump needed per docs/specs/persisted-json-schema-
versioning.md, still `klorb-session` `1.0.0`) the same way `session_id`/`session_name` do: every
`write_last_session()` call site (`ReplApp._quit_after_maybe_saving`,
`ReplApp._collect_hang_diagnostics`, `run_repl._handle_repl_crash`) passes
`live_session.root_id`/`cur_chainlink_task_id` through. `ReplApp._maybe_restore_last_session`
passes `state.root_id` to the reconstructed `Session`'s constructor alongside `session_id`/
`session_name`; `state.cur_chainlink_task_id` has no matching constructor argument, so it's set
via `set_chainlink_task()` on the reconstructed `Session` right after construction instead.

## Turn interjection

Whenever `TodoNext` picks a task, it registers a standing interjection
(`Session.register_standing_interjection("ChainlinkCurrentTask", ...)`) that's polled on every
subsequent `send_turn()` call, exactly like `BashTool`'s live-persistent-shell notice. Its
provider re-resolves the task's title fresh via `chainlink issue show` on every poll (`Session`
only stores the id, not the title) and returns `None` — ending the interjection — once
`cur_chainlink_task_id` is cleared or the issue can no longer be resolved. The message reminds
the model to comment on meaningful progress and to close the issue (then call `TodoNext` again)
once it's done and verified.

## TUI sidebar

`Ctrl+T` toggles a right-hand panel in the interactive REPL listing this session's issues
(open and closed, current task starred) — see docs/specs/terminal-repl.md's "Task sidebar"
bullet and `klorb.tui.widgets.task_sidebar.TaskSidebar`.

## Configuration

No new `klorb-config.json` keys. Whether `TASKS` tools are offered at all is determined purely
by whether the `chainlink` binary can be found (see "The `chainlink` binary", above) — not a
user-facing setting.

## Out of scope

* No permission gating on the `TASKS` tools (see "Tools", above).
* No cross-session "resume a prior session's leftover work" capability — a crashed or
  ungracefully-closed session's issues stay open under its label until manually cleaned up,
  since `close-all` only runs from `Session.close()`'s own teardown path. See
  docs/adrs/chainlink-issues-not-chainlink-sessions-for-continuity.md.
* No use of chainlink's own `session`/`timer`/`milestone`/`archive`/`cpitd`/`usage`/`agent`/
  `locks`/`sync`/`export`/`import` subcommands, or of `issue subissue`/`relate`/`unrelate`/
  `related`/`tree`/`tested`/`label`/`unlabel`/`delete`/`quick` — only `create`/`list`/`show`/
  `update`/`close`/`close-all`/`reopen`/`block`/`unblock`/`comment` are used.
* No subagent-spawning mechanism at all yet (see `klorb.role.Role.repertoire`), so `root_id`
  always equals a session's own `id` in practice today — the indirection (see "Session state",
  above) is in place for when that changes, not yet exercised by anything.
