# Chainlink task tracking

## Summary

A session's agent tracks its own fine-grained work as `chainlink` issues instead of (or ahead
of) prose planning: `TodoList`/`TodoNext`/`TodoCreate`/`TodoUpdate` (category `TASKS`) shell out
to the `chainlink` CLI, scoping every operation to one master session's issues via a `label`
equal to `Session.id`. State survives context compaction and a handoff between klorb sessions
through chainlink's own comments and dependency ("blocks"/"blocked by") graph — not through
chainlink's own Session/handoff-note/breadcrumb machinery, which klorb deliberately never uses;
see docs/adrs/chainlink-issues-not-chainlink-sessions-for-continuity.md.

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

Only `issue list`/`issue show`/`issue search` (and `session status`, unused here) honor
`--json`; every mutating subcommand (`create`/`update`/`close`/`reopen`/`block`/`unblock`/
`comment`/`close-all`) prints a plain-text confirmation line regardless of `--json`, so
`ChainlinkClient` passes `--quiet` to those instead and, where a value is actually needed
(`create`'s new id), parses `--quiet`'s bare-value stdout rather than trying to parse JSON that
was never produced. `issue block`, `issue create -w`, `issue close`/`close-all` (see "Setup",
below, for `--no-changelog`), and every other mutation return no payload `ChainlinkClient`
needs, so their stdout is discarded once the exit code confirms success.

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
  top-level `.gitignore` (creating the file if it doesn't exist) unless a line already covers it.
  `issues.db` has no merge-conflict resolution story — ids are sequential integers chainlink
  assigns itself, with nothing to reconcile across branches — so it must never be committed.
  (chainlink's own `init` already writes a `.chainlink/.gitignore` covering machine-local
  sub-files like `rules.local/`/`.cache/`/`agent.json` *within* `.chainlink/`, but that file does
  not cover `.chainlink/` itself or `issues.db`, so the workspace's own top-level `.gitignore`
  still needs the entry.)

There is no separate "run this before the first user message" hook wired into `cli.py`/the TUI's
several `Session`-construction call sites: every `Tool` in the `TASKS` category constructs a
fresh `ChainlinkClient` in its own `apply()` (see "Tools", below), so setup happens lazily,
exactly once, on whichever `Todo*` tool call happens first — functionally equivalent to running
it eagerly at session start, without threading a new call through five separate places.

## `ChainlinkClient`

`klorb.tools.tasks.common.ChainlinkClient` is the single place all four `Tool`s shell out
through — binary discovery, `--json`/`--quiet` handling, and lock-contention retry live here
once, not duplicated per tool (mirroring how `ModelRegistry`/`ToolRegistry` hold shared state
for their own callers). Constructed fresh by each tool's `apply()` from its own
`ToolSetupContext`, the same per-call-fresh pattern `Tool` itself follows
(`ToolRegistry.instantiate_tool`); never cached or shared across calls.

`chainlink` shares one SQLite file across concurrent sessions/subagents working in the same
workspace, so a locked-database error (`"database is locked"` in `stderr`, case-insensitive) is
retried: up to 4 attempts total, exponential backoff starting at 0.25s and doubling each retry,
plus uniform random jitter in `[-0.025s, 0.025s]` added to each wait. Any other failure (or a
lock-contention failure on the final attempt) raises `ChainlinkError` with `stderr`'s first
non-blank line.

`ChainlinkClient.__init__` requires a real `Session` on its `ToolSetupContext` (raises
`ValueError` otherwise, the same contract `BashTool._execute_persistent` enforces for
`session`-dependent functionality) — `label` is always `context.session.id`. It also registers
this session's close-time cleanup (`Session.register_teardown("ChainlinkClient", ...)`), which
runs `chainlink issue close-all --label <session_id> --no-changelog` when the session ends
(`atexit`, `/clear`, etc.) — see docs/adrs/chainlink-issues-not-chainlink-sessions-for-
continuity.md for why klorb uses chainlink this ephemerally today, with no "resume a prior
session's leftover work" capability yet.

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
* **`TodoNext`** — the same `fetch_and_sort_issues(include_closed=False)` pipeline, then the
  first issue (in sort order) with zero open blockers. Sets `Session.cur_chainlink_task_id` to
  that issue's id, or `None` if there isn't one. Three outcomes: `work_exists=False,
  project_complete=True, task=None` (no open issues at all — done); `work_exists=True,
  project_complete=False, task=None` (open issues remain but every one is still blocked —
  nothing actionable right now); or the picked issue. Registers a standing
  `<SystemInterjection subject="ChainlinkCurrentTask">` (see "Turn interjection", below) whenever
  it picks a task. `is_read_only()` is `False` despite only reading from chainlink — it mutates
  session state and registers a standing interjection as a side effect.
* **`TodoCreate`** — `chainlink issue create --quiet --priority PRIORITY --label LABEL
  [-d DESCRIPTION] TITLE`, parsing the new id from `--quiet`'s bare-value stdout, then
  `chainlink issue block`
  once per `blocked_by` id (the new issue is blocked by each), once for
  `blocks_current_issue=true` (`Session.cur_chainlink_task_id` — raises `ValueError` if unset —
  is blocked by the new issue), and once per `blocks_issues` id (that issue is blocked by the
  new one). Returns the new issue's full `issue show` detail.
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

`Session.cur_chainlink_task_id: int | None` (default `None`) tracks the issue id `TodoNext` most
recently picked. A plain public attribute (like `active_cancel_event`), not looked up per-tool
name through `tool_state` — every `Todo*` tool and the standing interjection provider read/write
it directly through `self.context.session`.

It round-trips through `last-session.json`
(`klorb.workspace.last_session.LastSessionState.cur_chainlink_task_id`, an additive optional
field — no `schema.version` bump needed per docs/specs/persisted-json-schema-versioning.md,
still `klorb-session` `1.0.0`) the same way `session_id`/`session_name` do: every
`write_last_session()` call site (`ReplApp._quit_after_maybe_saving`,
`ReplApp._collect_hang_diagnostics`, `run_repl._handle_repl_crash`) passes
`live_session.cur_chainlink_task_id` through, and `ReplApp._maybe_restore_last_session` sets it
directly on the reconstructed `Session` right after construction (there's no matching
constructor argument, unlike `session_id`/`session_name`).

## Turn interjection

Whenever `TodoNext` picks a task, it registers a standing interjection
(`Session.register_standing_interjection("ChainlinkCurrentTask", ...)`) that's polled on every
subsequent `send_turn()` call, exactly like `BashTool`'s live-persistent-shell notice. Its
provider re-resolves the task's title fresh via `chainlink issue show` on every poll (`Session`
only stores the id, not the title) and returns `None` — ending the interjection — once
`cur_chainlink_task_id` is cleared or the issue can no longer be resolved. The message reminds
the model to comment on meaningful progress and to close the issue (then call `TodoNext` again)
once it's done and verified.

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
* No subagent-aware label inheritance yet — klorb has no subagent-spawning mechanism at all
  today (see `klorb.role.Role.repertoire`), so `label` is always the top-level session's own id.
