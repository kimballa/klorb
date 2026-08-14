# Chainlink task tracking

## Summary

A session's agent tracks its own fine-grained work as `chainlink` issues instead of (or ahead
of) prose planning: `TodoList`/`TodoNext`/`TodoCreate`/`TodoUpdate` (category `TASKS`) shell out
to the `chainlink` CLI, scoping every list/close-all operation to one root session's issues via a
group `label` (`Session.get_chainlink_label()`, always `"group:<root_id>"`). State survives
context compaction and a handoff between klorb sessions through chainlink's own comments and
dependency ("blocks"/"blocked by") graph — not through chainlink's own Session/handoff-note/
breadcrumb machinery, which klorb deliberately never uses; see docs/adrs/chainlink-issues-not-
chainlink-sessions-for-continuity.md.

Within a group, each issue additionally carries a second label naming which agent owns it --
`"agent:<session-id>"` for a specific agent, or `"all"` while it's unclaimed -- so several agents
in the same group (a root session and its subagents) each see only their own slice of the task
list instead of an intermingled pool. See "Task assignment", below.

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

There is otherwise no separate "run this before the first user message" hook wired into
`cli.py`/the TUI's several `Session`-construction call sites: every `Tool` in the `TASKS`
category constructs a fresh `ChainlinkClient` in its own `apply()` (see "Tools", below), so setup
happens lazily, exactly once, on whichever `Todo*` tool call happens first -- deliberately not
run eagerly at every session's construction, since running `chainlink init` for a fresh workspace
is comparatively expensive (a real subprocess call) and most sessions never call a `Todo*` tool
at all.

The one exception: `CreateSubagentTool.apply()` calls `Session.ensure_chainlink_client()` on the
*creating* session right before dispatching a new subagent's turn, but only if that subagent's
own resolved tool set actually includes a `TASKS`-category tool. `ensure_chainlink_client()`
constructs a `ChainlinkClient` for the session it's called on at most once (memoized via
`Session._chainlink_client_ensured`, a plain flag check on every later call); only when that
session is itself the root does the construction register the group's close-time cleanup (see
"Session close-time cleanup", below). No walk up to the root is needed: a subagent can only ever
be created by a session that was itself already created the same way (`CreateSubagent` is the
only way a non-root session comes to exist at all), so the root always ends up with its own
`ensure_chainlink_client()` called the first time *it* creates a task-tracking subagent, before
any descendant several hops down could go on to create one of its own. A no-op (and so no
`chainlink init` cost) for a subagent role with no `TASKS` tool in its tool set -- today, every
subagent role -- or when the chainlink binary isn't available; any construction failure is
logged at `debug` and swallowed, since a real `Todo*` tool call will surface the same failure
later if the workspace is genuinely unusable.

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
`ValueError` otherwise) — `label` is always `session.get_chainlink_label()`, the group label
(see "Session state", below). Only when that `Session` is itself a root session (`session.parent
is None`) does it also register the group's close-time cleanup (`Session.register_teardown(
"ChainlinkClient", ...)`), which runs `chainlink issue close-all --label <group-label>
--no-changelog` when the session ends (`atexit`, `/clear`, etc.) — see docs/adrs/chainlink-
issues-not-chainlink-sessions-for-continuity.md for why klorb uses chainlink this ephemerally
today, with no "resume a prior session's leftover work" capability yet. A subagent's own
`Session` shares the same group label (see "Session state"), so if every `ChainlinkClient`
registered this callback, a subagent's own close (`cascade_close_subagents`, which runs before
the root's own teardown) would already close every issue in the group; restricting registration
to the root avoids that and keeps "who tears the group down" a single, well-defined answer.

`add_label`/`remove_label` (`chainlink issue label`/`issue unlabel`) are both idempotent —
adding an already-present label, or removing an absent one, exits `0` with a plain confirmation
line rather than an error (verified against the installed binary) — which is what lets the
claiming logic in "Task assignment", below, retry freely without special-casing "already applied".

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

* **`TodoList`** (read-only) — lists issues under this session's group label via
  `ChainlinkClient.fetch_and_sort_issues()`: `chainlink issue list --label <group-label> --status
  <open|all>` for the candidate id set, enriched per-id with `chainlink issue show <id>` (needed
  for `blocked_by`/`labels`, which `list` doesn't return), sorted by `issue_sort_key()` — open
  before closed, fewest open blockers first, highest priority first, then lowest id first. `ids`,
  if given as a single id, short-circuits straight to `issue show` (not subject to `scope`, since
  a caller already knows the specific id it wants); given as several, narrows the sorted result
  down to just those. `include_closed` includes closed issues (`--status all`), still sorted
  after every open one. `scope` (default `"self"`) further filters the full-list path to just
  this agent's own labeled issues; `scope="group"` returns every issue in the group instead — see
  "Task assignment", below.
* **`TodoNext`** — raises `ToolCallError` (category `"validation"`) up front if this session's
  role lacks the `accepts_tasks` capability (see "Task assignment", below). Otherwise first
  checks whether a current task is already tracked (`Session.cur_chainlink_task_id`) and, if that
  issue still resolves and is still open, returns *that same issue again* (does not pick a new
  one) with an extra `message` field telling the model to finish and close it first — `TodoNext`
  never silently abandons a task the model hasn't closed. Otherwise runs the
  `fetch_and_sort_issues(include_closed=False)` pipeline, narrows to issues with zero open
  blockers, and hands that ready list to `klorb.tools.tasks._util.claim_next_ready_task()` (see
  "Task assignment") to pick one this agent is actually eligible for, via
  `klorb.tools.tasks._util.set_current_task()`. Three outcomes overall: `work_exists=False,
  project_complete=True, task=None` (no open issues at all in the group — done);
  `work_exists=True, project_complete=False, task=None` (open issues remain but none both ready
  and eligible for this agent); or the picked (or claimed, or re-returned) issue. Registers a
  standing `<SystemInterjection subject="ChainlinkCurrentTask">` (see "Turn interjection", below)
  whenever it returns a task. `is_read_only()` is `False` — claiming an unclaimed issue writes
  chainlink labels; picking one already labeled for this agent, or re-returning the current one,
  only reads.
* **`TodoCreate`** — validates `priority`, (if `blocks_current_issue=true`) that a current task
  actually exists, and `assign_to` (see "Task assignment", below) *before* creating anything,
  then `chainlink issue create --priority PRIORITY --label <group-label> [-d DESCRIPTION] TITLE`
  (`--quiet`, parsing the new id from its bare-value stdout), then `chainlink issue label` with
  the resolved assignment label, then `chainlink issue block` once per `blocked_by` id (the new
  issue is blocked by each), once for `blocks_current_issue=true` (the current task is blocked by
  the new issue), and once per `blocks_issues` id (that issue is blocked by the new one). If the
  assignment label or any `block()` call fails partway through, the new issue is closed with a
  comment explaining why rather than left behind half-configured, and the original error is
  re-raised — creation is best-effort atomic. Returns the new issue's full `issue show` detail,
  plus an `active_task_note` field if `activate` (see "Auto-activation", below) picked it up as
  the session's current tracked task. Aliased as `TodoWrite`.
* **`TodoUpdate`** — dispatches whichever of its arguments are present to the matching
  chainlink subcommand against `id`: `new_title`/`new_description`/`new_priority` →
  `issue update`; `depends_on`/`drop_dependency` → `issue block`/`issue unblock` per id;
  `add_comment` → `issue comment`; `close`/`reopen` → `issue close --no-changelog`/`issue
  reopen`, applied last (so a comment added in the same call lands before the issue closes).
  Closing the issue that's also `Session.cur_chainlink_task_id` clears it
  (`Session.set_chainlink_task(None)`) as part of the same call — a closed issue can never be
  left tracked as the current task. Returns the updated issue's full `issue show` detail; when
  `close=True`, unless `activate=false`, that same call also immediately picks up whatever's
  next (see "Auto-activation", below) rather than requiring a separate `TodoNext` call. When the
  call doesn't close the issue, `activate` may instead pick the updated issue itself up as the
  session's current tracked task.

### Auto-activation

`TodoUpdate` and `TodoCreate` both accept an optional `activate: bool | None` argument.

When `TodoUpdate` doesn't close the issue, and for `TodoCreate` always, `activate` is resolved by
the shared `klorb.tools.tasks._util.maybe_activate_task()`: it can pick up the issue in question
(the one just updated, or just created) as `Session.cur_chainlink_task_id` — the same two steps
(`set_chainlink_task()` plus registering the standing interjection) `TodoNext` itself performs —
without a separate `TodoNext` call.

* `activate=false` never activates.
* Otherwise, the issue must be open.
* `activate=true` then activates unconditionally past that check.
* `activate` omitted ("auto mode") activates only if, in addition, the session has no current
  tracked task already and the issue has zero still-open blockers (`klorb.tools.tasks._util.
  task_is_ready()`) — the same state `TodoNext` itself requires before ever handing a task back.

If the issue still carries `ALL_LABEL` at this point, activation also claims it for this session
first (the same `_claim_one()` claim `TodoNext` performs), updating the issue detail's `labels`
in place before setting it current — otherwise it would stay unclaimed and up for grabs for
another agent's `TodoNext` even while this session already treats it as its own current task. If
that claim is lost to a same-instant race, activation doesn't happen at all.

When `TodoUpdate` *does* close the issue, `activate` instead controls whether the call
auto-advances to whatever's next: unless `activate=false`, `TodoUpdateTool.apply()` directly
constructs and calls a `TodoNextTool` against the same `ToolSetupContext` right after the close
(and after clearing `cur_chainlink_task_id` if the closed issue was the tracked one) — the exact
same tool a model would otherwise have to call itself. Its `task` field (if any) is folded into
the `TodoUpdate` result as `next_task_id`/`next_task_title` (both `None` if nothing is ready, or
every task is done), alongside an `active_task_note` summarizing the outcome
(`klorb.tools.tasks.todo_update._next_task_note()`). A closed issue is never itself a candidate
to become the new current task, since `maybe_activate_task()`'s "must be open" check would
exclude it — so this branch calls `TodoNextTool` directly instead of going through
`maybe_activate_task()`, which is why closing has no analogous "activate this issue" behavior.

None of the four apply any config-driven permission gate (unlike, say,
`tools.memory.writePermission`) — chainlink's SQLite file lives under the workspace's own
`.chainlink/` and is never read/written outside it, so there's no filesystem-boundary decision
to make the way there is for `ReadFile`/memory tools.

## Task assignment

Every issue in a group carries exactly one *assignment* label alongside its group label: either
`klorb.tools.tasks.common.agent_label(id)` (`"agent:<session-id>"`), naming the one agent that
owns it, or `ALL_LABEL` (`"all"`), meaning it's unclaimed and any eligible agent in the group may
claim it. This is what keeps several agents sharing one group (a root session and its subagents,
if subagent roles are ever granted `TASKS` tools) from seeing an intermingled task pool: each
agent only ever picks from issues labeled for it, or from `"all"`. `chainlink issue list` only
accepts one `--label` filter at a time, so this second label is never used to filter at the
chainlink CLI level — every `Todo*` tool still fetches the *whole* group via the group label
(`ChainlinkClient.fetch_and_sort_issues()`, which enriches every issue with its full `labels`
list via `issue show`) and filters by assignment label in Python.

Three `AgentCapabilities` fields (`klorb.agents.definition.AgentCapabilities`, part of each
`agents.json` role's `AgentDefinition.agent_capabilities`, read via `klorb.agents.registry.
get_agent_capabilities()`) gate what a role may do here — all default `False`, so a role must
opt in to each:

* `accepts_tasks` — whether a session running as this role may hold an issue as its own current
  tracked task. `TodoNext` raises `ToolCallError` (category `"validation"`) up front if this
  session's role lacks it; `TodoCreate` raises the same if `assign_to` resolves to `"self"`
  (explicitly, or by omission) for a role lacking it.
* `assigns_tasks` — whether a session running as this role may `TodoCreate` an issue with
  `assign_to` naming a *different* agent's session id.
* `see_group_tasks` — whether a session running as this role may `TodoList` with
  `scope="group"` to see every issue in the group, not just its own.

`TodoCreate`'s `assign_to` argument (resolved by `klorb.tools.tasks.todo_create.
_resolve_assignment_label()`) picks the new issue's assignment label:

* Omitted, `""`, or `"self"` — this session's own id, requiring its own role to have
  `accepts_tasks`.
* `"all"` — `ALL_LABEL`; always permitted.
* Any other string — treated as another agent's session id. Requires this session's role to have
  `assigns_tasks`; the id to resolve to a live `Session` somewhere in the same group
  (`klorb.agents.runtime.find_session_in_group()`, walking the tree from the group's root via
  `walk_session_tree()`), raising `ToolCallError` if no such agent exists; and that agent's own
  role to have `accepts_tasks`.

`TodoNext` picks a task in two passes over the ready (zero-open-blockers) list, via
`klorb.tools.tasks._util.claim_next_ready_task()`:

1. The first ready issue already labeled `agent_label(own_id)`, if any — no chainlink mutation
   needed, it's already this agent's.
2. Failing that, the first `ALL_LABEL`-labeled ready issue this agent successfully *claims*
   (`klorb.tools.tasks._util._claim_one()`): remove `ALL_LABEL`, add `agent_label(own_id)`
   (both idempotent -- see "`ChainlinkClient`", above), then re-fetch the issue to see who
   actually holds it now:
   * Only `agent_label(own_id)` present — this agent won, uncontested.
   * Some other single `agent:...` label present instead — another agent already claimed it
     first; back off and try the next `ALL_LABEL` candidate.
   * Two or more `agent:...` labels present — a same-instant race with another agent left the
     issue double-claimed. Every agent label is stripped and `ALL_LABEL` restored, then the
     attempt retries after a random `[0, 1s)` backoff, up to `_CLAIM_MAX_ATTEMPTS` (5) times
     before giving up on that candidate and moving to the next.

`TodoList`'s `scope` argument controls which of a group's issues it returns: `"self"` (default)
filters `fetch_and_sort_issues()`'s result to just `agent_label(own_id)`-labeled issues;
`"group"` returns every issue regardless of assignment, gated on `see_group_tasks`.

## AgentGroup interjection

`klorb.agents.runtime.build_agent_group_interjection_provider()` returns a standing interjection
closure registered by `SessionCoreMixin._register_agent_group_standing_interjection()` (called
from `_reset_state`, so it's active for both root sessions and subagents). On every
`send_turn()` call, the provider walks the full session tree (`walk_session_tree`, rooted at
the tree's top-level session), builds a markdown table with columns Role/Id/Title/State,
and compares it against a cached frozenset of ``(session_id, role_name, state)`` tuples. The
table is emitted (as a `<SystemInterjection subject="AgentGroup">`, see "Turn interjection",
below) on the first call (establishing the baseline) and again whenever the group's composition
or subagent activity changes; `None` (no interjection) is returned when the group is unchanged.

This is how a subagent learns the session ids it would need to pass as `TodoCreate`'s
`assign_to`, or a target for `MessageSubagent`, without a separate tool call. The `State`
column (`running`/`finished`) lets every agent in the tree see which peers are actively
processing and which are dormant.

## Session state

`Session.root_id: str` (default: the session's own `id`) names the root session a `Session`
descends from — itself for a top-level session, or its parent's `root_id` for a subagent (see
docs/specs/subagents.md). `get_chainlink_label()` returns `"group:<root_id>"`, and is the only
way `ChainlinkClient` learns what label to scope list/close-all operations by — it never reads
`session.id` directly. The indirection lets every session in a tree end up sharing one group
label, rather than each subagent scoping issues to its own, narrower `id`; the `"group:"` prefix
distinguishes this whole-tree label from the per-agent assignment labels layered on top of it
(see "Task assignment", above).

`Session.cur_chainlink_task_id: int | None` (default `None`) tracks the issue id most recently
picked (or re-returned) as the current tracked task — by `TodoNext`, by `TodoUpdate`'s
close-time auto-advance, or by `TodoUpdate`/`TodoCreate`'s auto-activation (see
"Auto-activation", above). Written only through `Session.set_chainlink_task(task_id)` — never
assigned directly to the attribute by a `Tool`; read directly (a plain public attribute, like
`active_cancel_event`) by `TodoCreate`'s `blocks_current_issue` handling and the standing
interjection provider.

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

Whenever a task becomes the session's current tracked task — `TodoNext` picking one, or
`TodoUpdate`/`TodoCreate`'s auto-activation (see "Auto-activation", above) — `klorb.tools.tasks.
_util.set_current_task()` registers a standing interjection
(`Session.register_standing_interjection("ChainlinkCurrentTask", ...)`) that's polled on every
subsequent `send_turn()` call, exactly like `BashTool`'s live-persistent-shell notice. Its
provider (`klorb.tools.tasks._util.standing_interjection_provider()`) re-resolves the task's
title fresh via `chainlink issue show` on every poll (`Session` only stores the id, not the
title) and returns `None` — ending the interjection — once `cur_chainlink_task_id` is cleared or
the issue can no longer be resolved. The message reminds the model to comment on meaningful
progress and that closing the issue (`TodoUpdate close`) once it's done and verified
automatically picks up whatever's next — see "Auto-activation", above.

`klorb.tools.tasks._util` is where this mechanism (and the shared auto-activation logic in
"Auto-activation", above) lives, so `TodoNext`'s own `apply()` and `TodoUpdate`/`TodoCreate`'s
each perform it via the same two shared functions rather than duplicating the "set the id,
register the interjection" steps three times over.

## TUI sidebar

`Ctrl+T` toggles a right-hand panel in the interactive REPL listing this session's issues
(open and closed, current task starred) — see docs/specs/terminal-repl.md's "Task sidebar"
bullet and `klorb.tui.widgets.task_sidebar.TaskSidebar`.

`ChainlinkClient.fetch_and_sort_issues()` has a second consumer besides the TUI sidebar: the ACP
server maps its result onto a standard ACP `plan` session update after every `Todo*` tool call
and once per `session/new` (when a chainlink database already exists for the workspace) — see
docs/specs/klorb-server.md's "Chainlink task-plan updates" section. Both of these fetch the
group's *whole* issue list (no `scope` filtering), same as before per-agent assignment existed.
`TASK_TOOL_NAMES` (the tool names that can change the list) and `chainlink_db_exists()` (the "is
there already a database" check) live in `klorb.tools.tasks.common` precisely so both consumers
share them.

## Configuration

No new `klorb-config.json` keys. Whether `TASKS` tools are offered at all is determined purely
by whether the `chainlink` binary can be found (see "The `chainlink` binary", above) — not a
user-facing setting.

## Out of scope

* No permission gating on the `TASKS` tools (see "Tools", above).
* No cross-session "resume a prior session's leftover work" capability — a crashed or
  ungracefully-closed session's issues stay open under its label until manually cleaned up,
  since `close-all` only runs from `Session.close()`'s own teardown path. See
  docs/adrs/00148-chainlink-issues-not-chainlink-sessions-for-continuity.md.
* No use of chainlink's own `session`/`timer`/`milestone`/`archive`/`cpitd`/`usage`/`agent`/
  `locks`/`sync`/`export`/`import` subcommands, or of `issue subissue`/`relate`/`unrelate`/
  `related`/`tree`/`tested`/`delete`/`quick` — only `create`/`list`/`show`/`update`/`close`/
  `close-all`/`reopen`/`block`/`unblock`/`comment`/`label`/`unlabel` are used.
* No reassignment of an already-assigned issue — `TodoCreate`'s `assign_to` only sets the
  assignment label at creation time; there's no `TodoUpdate` argument to hand an existing issue
  off to a different agent afterward.
