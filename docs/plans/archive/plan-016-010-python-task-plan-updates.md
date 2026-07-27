# Plan 016, increment 010: Python task tracking as ACP plan updates

> **Draft.** Part of [Plan 016](plan-016-acp-client-server.md). Do not implement from the
> `drafting/` folder.

## Goal

Klorb's chainlink-backed task tracking (docs/specs/chainlink-task-tracking.md) becomes
visible to ACP clients through the protocol's standard `plan` session update: whenever the
session's issue list may have changed, the server emits the full current plan — entries with
content, priority, and status — plus klorb detail (issue ids, blockers, the current starred
task) in `_meta.klorb`, so a stock ACP client sees a sensible plan while the klorb client
(increment 011) renders the richer task panel.

## Deliverables

### 1. Plan snapshot builder (`klorb/src/klorb/server/plan_updates.py`)

* `build_plan_update(issues, cur_task_id) -> <acp plan update>`: maps a
  `fetch_and_sort_issues()` result (already sorted: open first, fewest open blockers,
  priority, id — reuse `issue_sort_key`, don't re-sort) to ACP plan entries:
  * `content` = `"#<id> <title>"` (the TUI sidebar's row format).
  * `priority`: map chainlink `PRIORITY_ORDER` onto ACP's plan-entry priority values
    (high/medium/low; chainlink's finer grades collapse via a small explicit dict, with
    the exact collapse recorded in the spec).
  * `status`: closed issue → `completed`; `id == cur_task_id` → `in_progress`; else
    `pending`. (Blocked-ness is *not* a distinct ACP status — it rides in `_meta`.)
  * Per-entry `_meta.klorb = {issueId, openBlockerCount, blockedBy: [...], closed:
    bool, isCurrentTask: bool}`; update-level `_meta.klorb = {openCount, closedCount,
    blockedCount, currentTaskId}` so a client can render the collapsed one-line summary
    without walking entries.
* Pure function; the chainlink I/O stays at the caller.

### 2. Refresh triggers (in `TurnBridge` / `KlorbAcpAgent`)

Mirror the TUI sidebar's refresh policy (`_maybe_refresh_task_sidebar_after_tool_call`),
but without its "only while visible" gate (the server can't see panel visibility, and one
notification per mutation is cheap):

* After any finished tool call whose `ToolCallEvent.name` ∈
  {`TodoList`, `TodoNext`, `TodoCreate`, `TodoUpdate`}, run
  `fetch_and_sort_issues(include_closed=True)` on the worker thread (chainlink shells out
  synchronously — same reasoning as the TUI's thread-worker refresh) and enqueue the plan
  update on the ordered outbound queue.
* Once per `session/new`, emit an initial snapshot **only if** a `.chainlink/issues.db`
  already exists in the workspace (`chainlink_available()` and the db-file check —
  never trigger `chainlink init`'s scaffolding just to report an empty plan).
* Chainlink unavailable / `ChainlinkError`: emit nothing, `logger.debug` the reason —
  parity with the sidebar's "not available" placeholder is the client's concern (011
  shows it when it has never received a plan update but the server advertised
  `taskMeta`).
* Advertise `agentCapabilities._meta.klorb.taskMeta = true` (meaning: plan updates carry
  the klorb `_meta` detail contract) when the chainlink binary is discoverable at
  initialize time.

### 3. Docs

Spec updates in `docs/specs/klorb-server.md` (mapping tables, triggers, meta contract) and
a cross-reference note in `docs/specs/chainlink-task-tracking.md`'s TUI-sidebar section
(the ACP plan surface as the second consumer of `fetch_and_sort_issues`).

## Tests

* `test_plan_updates.py` (pure): entry mapping (open/closed/current/blocked
  permutations), priority collapse, meta counts, ordering preserved from input.
* `test_acp_server_plan.py` (harness): fake the chainlink seam (`ChainlinkClient` /
  `fetch_and_sort_issues` monkeypatched — the tools' own behavior is already covered by
  the tasks test suite): a scripted turn calling `TodoCreate` then `TodoUpdate(close)`
  produces two plan updates whose entries/statuses track the fake's state; a
  non-`Todo*` turn produces none; new_session with a pre-existing fake db emits the
  initial snapshot; `ChainlinkError` on refresh emits nothing and doesn't fail the turn.

## Checkpoint criteria

* `make -C klorb lint typecheck test` green.
* Manual: in a workspace with chainlink installed, prompt the agent to plan work with
  todos; watch `plan` session updates stream in the hand-driven client.
* Client (through 009) unaffected — it ignores `plan` updates until 011.
