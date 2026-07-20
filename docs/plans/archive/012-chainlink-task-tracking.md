# Plan: Use Chainlink for task tracking

The agent should be guided in a task execution-centric loop.
We use *chainlink* to ensure it stays on track.

## Chainlink

Chainlink is a todo item tracking system for agents in a source control repository.

* We shell out to the `chainlink` binary.
* We always pass `--json` to get json-only output.
* Binary discovery: check `which chainlink` (PATH) first; if that finds nothing, fall back to
  `$HOME/.cargo/bin/chainlink`.
  * `make cloud_setup` installs chainlink for cloud/remote-agent environments.
  * Vendoring the binary for distribution to other users' machines is a future concern, blocked
    on klorb having any packaging story at all -- not in scope here.
* We do not use chainlink's own Session/handoff-note/breadcrumb feature -- only Issues (comments
  and the dependency graph), which is what carries task state across context compaction or a
  handoff between klorb sessions. See
  docs/adrs/chainlink-issues-not-chainlink-sessions-for-continuity.md for why.

## Setup

When operating in a workspace where we have write access (i.e., it's trusted), we have to run
`chainlink --json init` before using it. Do this before sending the first user message.

We do this if we cannot find `${wsroot}/.chainlink/issues.db`.

If the `chainlink` binary cannot be found by either lookup above, log an error and skip this step
entirely -- the `TASKS` tool category is not registered for the session (it's simply absent from
`ToolRegistry.discover_tools`'s result), so no Todo* tool call can ever be attempted.

Setup must also ensure `.chainlink/issues.db` is gitignored (add a `.chainlink/` entry to the
workspace's `.gitignore`, creating the file if it doesn't exist, if not already covered by an
existing rule). Issue ids are sequential integers assigned by chainlink itself with no
merge-conflict resolution story, so this file must never be checked into version control --
committing it would make two branches' issue databases irreconcilable on merge.

## Tools

We introduce a chainlink-centric set of tools into the agent environment. All tools here have the
category name `TASKS`. `TodoList` is read-only (`is_read_only() = True`); `TodoNext`, `TodoCreate`,
and `TodoUpdate` all report `is_read_only() = False` -- `TodoNext` only *reads* via
`chainlink issue show`, but it mutates the session's tracked current-task id as a side effect (see
"Session state" below), so it doesn't qualify as read-only either.

All four tools delegate their `chainlink` subprocess invocations to a single `ChainlinkClient`
class (constructed once per `ToolSetupContext`, mirroring how `ModelRegistry`/`ToolRegistry` hold
this kind of shared state) rather than each tool shelling out independently -- binary discovery,
`--json` handling, and retry-on-lock-contention all live there, once.

### Retrying on SQLite lock contention

`chainlink` shares one SQLite file across concurrent sessions/subagents, so a locked-database
error is retried by `ChainlinkClient`: up to 4 attempts, exponential backoff starting at 0.25s and
doubling each retry, plus uniform random jitter in `[-0.025s, 0.025s]` added to each wait.

* *TodoList* -- List todo items associated with this session's work.
  * Input can specify an id or list of ids to filter down to.
  * Agent can ask for all the issues or just the open ones.
  * **always** filters by the label associated with the master session. Each master session is its own little world.
    This is a consistency measure, not a security boundary -- labels are enumerable/guessable, and multiple klorb
    sessions are inherently collaborating on the same local repo. It exists so that concurrent sessions (e.g.
    across worktrees, future work) don't clobber each other's issues, not to restrict access.
  * If given just 1 id, use `chainlink issue show`, otherwise `chainlink issue list`.
  * Chainlink already returns structured json data, which we can pass back directly. But we should enforce that
    the TODO list is sorted, because agents really care a lot about sort order.
  * Sort by:
    * open items first, closed ones later (if closed are listed)
    * number of blockers in the way, ascending (0-blocker / ready items at the top)
    * priority, descending
    * id, ascending (at same priority, older items listed first)

* *TodoNext* -- Return the next todo item to work on.
  * Returns: { work_exists: bool, project_complete: bool, task: { `chainlink issue show <id>` } }
  * work_exists is false (and project_complete is true) when we're all done. in which case task is null.
  * This sets `Session.cur_chainlink_task_id: int | None`, a new field on `Session` itself (not
    `SessionConfig`) tracking the id of the task currently being worked on. See "Session state"
    below.
  * (Eventually this will respect assignments to subagents; it'll return the next unblocked thing that
    isn't already assigned.)

* *TodoCreate* -- Add another todo list item
  * args
    * title for new task
    * description (optional)
    * priority (optional) low/medium/high/critical, default medium
    * list of incomplete task ids that it is blocked by (optional, default empty)
    * blocks_current_issue: bool - true if whatever is our current issue id is blocked by this one; use `issue block`
      to record this upfront.
    * blocks_issues: list[id] - list of ids that will depend on this one. (use `issue block`)
  * Returns the `chainlink issue show <new-id>`
  * If this is run by the Operator, we use our session_id as the label to apply to the issue.
    If we are running in a subagent, the Operator will have given us that label and we use it too.
    This keeps all issues from the same master session associated with one another.

* *TodoUpdate* -- update a todo item
  * args
    * close: bool - if true, close the issue
    * reopen: bool - if true, reopen a closed issue (`chainlink issue reopen`)
    * depends_on: list[int] - add dependencies by id
    * drop_dependency: list[int] - remove dependencies by id, the inverse of `depends_on`
    * add_comment: str - add a comment
    * new_title - update title.
    * new_description - update desc.
    * new_priority: Priority - update the priority
  * any of those args can be null; those are just ignored. this is a set of operations that can be performed
    with `chainlink --json --quiet issue update <update-flags> <id>`
  * `depends_on` is used to create `chainlink --json --quiet issue block <blocked_id> <blocked_by_id>` relationships.
  * `drop_dependency` is used to remove them via `chainlink --json --quiet issue unblock <blocked_id> <blocked_by_id>`.
  * output is `chainlink --json issue show <id>` result.

## Session state

`Session` gains a `cur_chainlink_task_id: int | None` field, set by `TodoNext` and read by the
turn interjection (see "Turn interjections" below).

* This field is part of the `last-session.json` restore path (docs/specs/session-persistence.md):
  `LastSessionState` gains a matching optional `cur_chainlink_task_id: int | None = None` field,
  `write_last_session` includes it, and `ReplApp._maybe_restore_last_session` sets it directly on
  the reconstructed `Session` alongside `session_id`/`session_name`. This is a purely additive
  field with a default, so it round-trips fine against older save files with no `schema.version`
  bump needed (per docs/specs/persisted-json-schema-versioning.md, still `klorb-session` `1.0.0`).
* Future work: a "take over a prior session's leftover work" capability, where a new session can
  attach to a still-open label from an earlier (possibly crashed, or ungracefully closed) session
  and resume its open issues instead of the `close-all` behavior described below. Not built here.

## Cleanup

We are currently using chainlink in an ephemeral way where different sessions don't really interact
with one another.

When the session is closed (either via atexit, `>clear`, etc), we should issue
`chainlink issue close-all --label <session_id>`. See "Session state" above for the planned future
capability that will change this.

## Prompts

### Default system prompt

Explain how the Todo* tools work.

### Operator System prompt

Explain that the agent values keeping its work organized and should use its Todo-specific tools to do so.

* Whenever in the "Plan" phase of the work loop, it should identify the tasks to do and record them
  with TodoCreate.
  * Marking dependencies between tasks will make it easier to determine what to work on next at any step.
* Whenever in the "Report" phase:
  * Add comments to the issue for anything important to keep in mind for the next turn of the loop based
    on all the work that was just performed.
  * If the issue is complete and verified, close it.
* ... all this should revise or replace the "Decompose large problems" sys prompt section.

* also mention that if the todo list is empty and everything is closed, that's a good signal that your work is done.

### Turn interjections

We track which task id the agent is working on.

* The current task id and title is always put into a SystemInterjection.
* The system interjection encourages the agent to record a comment on the issue whenever it has made meaningful
  progress, learned something new, got the answer to a question, or made a decision.
  * Encourage it to create new todo items whenever it identifies a question it needs to resolve,
    and mark them as blockers for whatever prompted the question.
  * Encourage it to close the issue if it's done, then use TodoNext to get the next task.
