# Subagents

## Summary

A subagent is a bounded-task specialist session that an agent (the "creator") can launch and
receive a report back from — `CreateSubagent` and `WaitForSubagent` are the tools that do this
(`klorb/src/klorb/tools/subagents/`). A subagent runs as its own `Session`, with its own role,
system prompt, and (usually narrower) tool/skill access, but it is not a standing team member:
it answers one request and sits dormant until asked a follow-up or the whole session tree closes.
Any agent that has one may also converse directly with *any* other agent in the same session
tree — creator, subagent, or sibling alike — via `SendMessage`/`GetMessages`; see "Agent-to-agent
messaging" below.

Several roles exist today for a subagent to run as -- `klorb/src/klorb/resources/agents.json`
defines each one's capability policy, and `operator`'s own `restrict_to.subagent_roles` entry
there names which of them it may launch. Only the `operator` role (the top-level, user-facing
session) is permitted to launch any of them at all. See "Explorer role", "Reviewer role",
"Implementer role", and "Pair Programmer role" below for a few of them in more depth.

## Configuration

Three `ProcessConfig` fields under the `tools.subagents.*` `klorb-config.json` namespace, plus
one under `tools.messaging.*`:

* `subagents_max_depth` (`tools.subagents.maxDepth`, default `2`) — how many subagent hops
  below the top-level session (`Session.depth == 0`) a tree may nest.
* `subagents_max_concurrent_per_parent` (`tools.subagents.maxConcurrentPerParent`, default `4`)
  — the most subagents any single session may have *running* at once (see "Subagent lifecycle"
  below for what "running" means).
* `subagents_max_active_total` (`tools.subagents.maxActiveTotal`, default `16`) — the most
  subagents that may be running simultaneously across an entire session tree, regardless of
  which session in the tree created each one.
* `messaging_max_queue_size` (`tools.messaging.maxQueueSize`, default `100`) — the most
  undelivered `SendMessage` entries the whole session tree's `AgentMessageQueue` may hold at
  once; see "Agent-to-agent messaging" below.

`klorb/resources/agents.json` (schema envelope `klorb-agents`) defines each subagent role's
capability policy, parsed once at process start into an immutable `AgentRegistry`
(`klorb.agents.registry.get_agent_registry()`) — a running process never re-reads the file, so
editing it on disk can't retroactively loosen restrictions already computed for live sessions.
Each entry is an `AgentDefinition` (`klorb.agents.definition`):

```json
{
  "name": "explorer",
  "default_model": "klorb-default/fast",
  "restrict_to": {
    "tools": ["ReadFile", "Grep", "..."],
    "tool_categories": ["..."],
    "skills": [],
    "subagent_roles": ["explorer", "vision_assistant"],
    "enforce_readonly_tools": true
  },
  "allow_subagents": false,
  "max_copies": 1,
  "agent_capabilities": {
    "accepts_tasks": false,
    "assigns_tasks": false,
    "see_group_tasks": false
  },
  "hooks": {},
  "events": {
    "FileSystemModified": [
      { "watch": ".", "action": { "type": "chat", "prompt": "..." } }
    ]
  }
}
```

* `allow_subagents` — whether a session running as this role may itself call `CreateSubagent`.
  Also drives whether the three subagent-management tools are included in a subagent's own
  computed tool set at all (see "Security model").
* `max_copies` — the most sessions running as this role allowed anywhere in one session tree at
  once. `None` or `-1` means uncapped; `0` means the role may never be launched. `operator`,
  `implementer`, `reviewer`, `planner`, and `pair_programmer` are capped at `1`; `explorer` is
  explicitly uncapped.
* `restrict_to` (an `AgentRestrictions`) narrows what a subagent of this role inherits from its
  creator — every field is optional and, left unset (`None`), means "inherit everything the
  creator has"; an explicit empty list means "inherit nothing," a deliberately different value
  from unset:
  * `tools` — tool names to keep, intersected against the creator's own effective tool set.
  * `tool_categories` — `Tool.category()` values to keep, applied on top of `tools` (or instead
    of naming tools individually) — lets a role admit whole categories, including tools that
    don't exist yet.
  * `skills` — fully-qualified skill names (`"<namespace>:<name>"`) to keep.
  * `subagent_roles` — role names this subagent may itself pass to `CreateSubagent`.
  * `enforce_readonly_tools` — clamp the tool set (after `tools`/`tool_categories`) to only
    tools reporting `Tool.is_read_only() == True`.
* `agent_capabilities` (an `AgentCapabilities`, `klorb.agents.definition`) — capabilities for
  this role, all defaulting `False`: `accepts_tasks` (may hold a chainlink issue as its own
  current task), `assigns_tasks` (may `TodoCreate` an issue assigned to a *different* agent),
  `see_group_tasks` (may `TodoList` with `scope="group"`), `send_messages` (may `SendMessage`
  another agent — see "Agent-to-agent messaging" below; any role may *receive* a message and use
  `GetMessages` regardless of this flag). Read via `klorb.agents.registry.
  get_agent_capabilities()`. Distinct from `restrict_to`, which narrows tool/skill/subagent-role
  *inheritance* rather than this kind of behavior — a role can hold a tool in its effective tool
  set yet still be refused the capability it gates, e.g. a role with `SendMessage` in its tool set
  but `send_messages: false`. See docs/specs/chainlink-task-tracking.md's "Task assignment"
  section.
* `hooks`/`events` — the same `{name: [handler, ...]}` shape a skill's `metadata.klorb.hooks`/
  `.events` frontmatter grant carries (docs/specs/skills.md), granted onto every subagent created
  as this role. See "Role-granted hooks/events" under "Pair Programmer role" below.

## Addressing

The top-level session has address `"1"`. A session's subagents are numbered `1.1`, `1.2`, `1.3`,
…, monotonically as `CreateSubagent` succeeds and never reused — `Session._allocate_child_index()`
increments `_next_child_index` once per subagent ever created, so an address stays a stable
reference to one specific subagent for as long as the process runs, even after that subagent
finishes. `Session.address()` recomputes the full dotted-decimal string from the live `parent`
chain on every call rather than caching or persisting it — it's a human-facing display label
only; no tool takes an address as an argument.

## AgentGroup interjection

A standing `<SystemInterjection subject="AgentGroup">` (registered via
`klorb.agents.runtime.build_agent_group_interjection_provider`, polled on every `send_turn()`)
emits a markdown table of every agent in the session tree whenever group composition or subagent
activity changes — see docs/specs/chainlink-task-tracking.md's "AgentGroup interjection" section
for the full mechanism. This is how a subagent learns the session ids it needs for `TodoCreate`'s
`assign_to` or a `SendMessage` target, without a dedicated lookup tool.

## Security model

The core invariant: a subagent must never be able to do anything its creator cannot, and a
multi-level chain of subagents must never be able to recover a privilege an intervening level
deliberately stripped. This invariant starts at the root: a root (top-level, user-facing)
session's own grants are themselves computed by intersecting its role's `agents.json` entry
against the unrestricted universal catalog (see "Root session grants" below), rather than the
root session simply being handed everything unconditionally — so "what a creator has" is always
a real, `agents.json`-derived quantity, at every level of a session tree, not a special case at
the top.

`klorb.agents.intersection` (`compute_child_tool_set`/`compute_child_skill_set`/
`compute_child_subagent_roles`) is the pure, unit-tested engine every narrowing computation runs
through. Critically, every call site (`klorb.agents.policy.plan_subagent_creation`) passes the
*creator's own live, already-narrowed effective sets* — its actual `tool_registry`, its actual
`config.skill_rules`, its own `Session.effective_subagent_roles` — never a fresh `agents.json`
lookup of the creator's role name alone. A session's effective sets are already the accumulated
intersection of every restriction applied since the root session, so re-deriving from a role
name at each hop could recover privileges an ancestor deliberately stripped.

* **Tools**: `plan_subagent_creation` builds `{tool_name: ToolMetadata(category, is_read_only)}`
  from the creator's own `tool_registry.tools()`, intersects it via `compute_child_tool_set`,
  then strips `CreateSubagent`/`WaitForSubagent`
  (`klorb.agents.runtime.SUBAGENT_MGMT_TOOL_NAMES`) unless the *child's own role* has
  `allow_subagents: true`. `SendMessage`/`GetMessages` are not part of that stripped set — a
  child's ability to send is gated purely by its own role's `send_messages` capability (checked
  at call time), and any role may receive and read messages. The child's `ToolRegistry` is then
  built directly
  (`ToolRegistry(process_config, child_config, tool_classes)`) from that filtered class map —
  never a fresh package discovery.
* **Skills**: the creator's currently-discoverable skills
  (`resolve_session_skill_catalog_registry(context).canonical().discoverable(...)`) are
  intersected via `compute_child_skill_set`; whichever names fall out of the intersection are
  added to the child's `SessionConfig.skill_rules.deny` on top of whatever the creator's own
  rules already deny. This is a snapshot taken at creation time, matching how the child's
  `ToolRegistry` is likewise a one-time snapshot.
* **Subagent roles**: `Session.effective_subagent_roles` (a `frozenset[str]`, empty by default)
  is computed once, at a session's own construction, and stored on it: via
  `compute_child_subagent_roles` for a subagent (intersected against its parent's own
  `effective_subagent_roles`), or via `compute_root_session_grants` for a root session
  (intersected against every role `agents.json` defines — see "Root session grants" below). A
  later `CreateSubagent` call always reads this already-computed field directly, never a fresh
  lookup of the calling session's own role's nominal `subagent_roles`.
* `CreateSubagent`/`WaitForSubagent`/`SendMessage`/`GetMessages` all report `is_read_only() ==
  True` — they don't mutate file or environment state directly; a subagent's actual capabilities
  are bounded by the intersection above, not by whether these tools are offered under
  `enforce_readonly_tools`. `EditScratchpad` also reports `is_read_only() == True` for a related
  reason: the scratchpad is harness-managed shared workspace state, not the user's own files or
  environment, so a read-only-enforced role (like Explorer) can still use it to collaborate.
* **`@mention` skip-fallback**: `Session.send_turn()` takes a `resolve_mentions: bool = True`
  parameter; `klorb.agents.policy._run_subagent_turn()` always calls it with
  `resolve_mentions=False`, for every subagent turn regardless of how it was started —
  `CreateSubagent`'s `initial_message`, a `SendMessage` delivery, or a chained hook continuation
  alike — leaving any `@filename` text in the message literal rather than resolving it into a
  file-content fragment. This avoids handing a subagent file content the creator's own
  `readDirs` rules might not actually allow it to read directly.
* **Permission asks**: a subagent's background turn runs with `TurnEventHandlers` built by
  `klorb.agents.policy.build_subagent_turn_handlers` — no streaming/progress callbacks (nothing
  renders a subagent's turn directly today), but every ask-style callback
  (`on_permission_ask`/`on_ask_user_questions`/`on_escalate_privileges`) forwarded to whichever
  callback the *creating* session's own current turn is using (`Session.current_turn_handlers()`,
  read at dispatch time), with the ask's human-readable text field prefixed
  `[subagent <address> (<role>)]` and its `origin_session_id` stamped with the leaf subagent that
  actually raised it (see "Subagents panel" below for how the TUI uses that id). This routes a
  subagent's interactive asks through the same single-session UI the creator's own turn already
  uses — ultimately, always the root session's own `ReplApp` callbacks, since every level's
  forwarding closure just relays to its own creator's `current_turn_handlers()` — rather than a
  dedicated per-subagent transport.

### Root session grants

A root session has no real parent `Session` to compute its effective tool/skill/subagent-role
sets from — every construction site (`klorb.cli.main`, `klorb.tui.app.ReplApp.__init__`,
`klorb.tui.mixins.prompt_submission.clear_session`,
`klorb.tui.mixins.workspace_bootstrap.load_recent_session`,
`klorb.server.klorb_agent.KlorbAcpAgent.new_session`, `klorb.session.restore.
try_restore_session`) calls `klorb.agents.policy.compute_root_session_grants(process_config,
session_config, role_name)` first, which:

* Builds `everything` via `ToolRegistry.discover_tools()` — the full, unfiltered package scan.
* Looks up `role_name`'s own `agents.json` entry (e.g. `"operator"`) and runs its `restrict_to`
  through the *same* `_child_tool_classes`/`_child_skill_rules`/`compute_child_subagent_roles`
  a subagent's own creation uses, with `everything` (plus every skill on disk, plus every role
  `agents.json` defines) standing in for "the parent's own effective sets."
* Returns a `RootSessionGrants` (`tool_registry`, `skill_rules`, `effective_subagent_roles`) that
  the caller passes straight into `Session(...)` — the root session's `tool_registry` is
  therefore already the filtered registry, never the raw `everything` scan, and its
  `effective_subagent_roles` is populated at construction exactly like a subagent's, never left
  for `CreateSubagent` to patch around later.

A role with no `agents.json` entry gets an unrestricted `AgentRestrictions()` (today's behavior
for an undefined role — e.g. a typo'd `--role`) but no subagent-launch ability, since
`allow_subagents` defaults to `False`. Because `operator`'s own `restrict_to` currently sets only
`subagent_roles` (no `tools`/`skills`), a root operator session's tool/skill sets are unaffected
in practice — `restrict_to.tools`/`.skills` being `None` still means "inherit everything" — but
the mechanism is the uniform one, not a root-only special case, so tightening `operator`'s entry
in `agents.json` in the future would actually take effect.

## Subagent session model

A subagent's `Session` is constructed directly (never by cloning the parent `Session` object):

* `SessionConfig` is `parent.config.model_copy(deep=True)` — a true, independent copy of every
  field (so a later grant on one session never retroactively affects the other) — except
  `permission_framework_state`, restored to the *same* shared-by-reference object the parent
  holds immediately after the deep copy. This is deliberate: `permission_framework` is a live
  UI toggle (Shift+Tab in the TUI) the user expects to apply to every session in the tree at
  once, so it's the one exception to "clone and diverge independently" (see
  docs/specs/permissions.md).
* `role_name` is overwritten to the requested subagent role; `model` is the `CreateSubagent`
  call's `model` argument if given, else the role's `default_model`. Either value may be one of
  the `klorb-default/fast`/`normal`/`heavy` placeholders (`klorb.models.placeholders`), resolved
  against `ProcessConfig.default_model_fast`/`default_model_normal`/`default_model_heavy`, or
  `klorb-default/current`, resolved to the creating session's own `config.model`.
* `skill_rules` is the computed deny-list from "Security model" above.
* On the `Session` object itself (not part of the copied config): `parent` is a reference to the
  creator; `depth = parent.depth + 1`; `effective_subagent_roles` is the computed, stored set;
  `scratchpad_path` is `str(parent.scratchpad.path)` — the *same* on-disk file, not a fresh one
  (a subagent's `Scratchpad` therefore never owns or removes that file; only the root session's
  does); `session_name` is pre-set from `CreateSubagent`'s `session_title` argument, which skips
  the one-shot session-naming classifier (that only fires when a session is constructed with no
  name at all) — so a subagent's `id` is simply its own timestamp + coolname nonce, same as a
  root session's, while its display `name` comes from the creator-supplied title.
* `max_output_tokens`, if given to `CreateSubagent`, is threaded straight into the child
  `Session` and applied as `max_tokens` on every `ApiProvider.send_prompt()` call it makes —
  a cap on total generated tokens (reasoning plus completion, for a model that bills both
  against one budget), independent of any `klorb-config.json` setting.

## Subagent lifecycle

A `klorb.agents.runtime.SubagentHandle` tracks one subagent from the creating session's own
`Session.subagent_tracker` (a `klorb.agents.runtime.SubagentTracker`, one per `Session`, never
shared): the child `Session`, its background `threading.Thread`, its own dedicated
`cancel_event`, and three independent axes of state:

* `state`: `"running"` while its background turn is actively processing, `"finished"` once that
  turn ends. A subagent session is **never destroyed** once finished — it sits dormant,
  `SendMessage` (or a human, see "Direct user messaging" below) can resume it later (flipping
  it back to `"running"`), and there is no tool to explicitly close one. Only ending the
  *creating* session tears a subagent down (see "Persistence" below).
* `delivered`: whether the subagent's completed output has already been handed to the creating
  session — via `WaitForSubagent` or (at shutdown) `cascade_close_subagents`'s direct-append
  fallback. A `"finished"` subagent is not `delivered` until one of those actually consumes it,
  and stays dormant either way.
* `parent_interested`: whether the creating session is the one currently expecting this
  subagent's eventual output. Set at construction time by whichever call dispatched the turn this
  handle represents: `CreateSubagent` and `try_wake_next_queued_agent()` (the `SendMessage`
  scheduler — see "Agent-to-agent messaging" below) both set it to whether the *sender* of the
  message that triggered the dispatch is this subagent's own `parent`, so a sibling or
  grandparent messaging a dormant subagent does not silently redirect its output to the actual
  parent; `klorb.agents.policy.dispatch_direct_message`'s fresh-turn branch (a human resuming a
  dormant subagent) always sets it `False`. Once a handle exists, `SubagentTracker.
  mark_parent_interested()` can flip it back to `True` in place — without dispatching a new turn
  — whenever a message from this subagent's own `parent` actually reaches it while the handle is
  still `"running"`, whether via `GetMessages` or the end-of-turn drain (see "Agent-to-agent
  messaging"). A human's mid-turn interjection into an already-running, parent-dispatched turn
  does not change it (see "Direct user messaging"). Only a `parent_interested` completion is ever
  queued for delivery to the parent — see "Communicating back to the parent".

**"Concurrent" and "active" both mean `state == "running"`, not "undelivered."** A
finished-but-undelivered subagent consumes no compute and is not "in flight" — it must not
itself block a new one from being created just because nobody has collected its output yet.
`SubagentTracker.running_count()` (bounds `maxConcurrentPerParent`) and
`klorb.agents.runtime.total_active_subagents()` (walks the whole tree, bounds `maxActiveTotal`)
both count only `"running"` handles accordingly. `klorb.agents.policy.check_concurrency_limits()`
is the check `CreateSubagent` runs before starting a background turn, raising
`ToolCallError(category="transient")` — not `"validation"` — since the fix is to wait for an
existing subagent to finish (`WaitForSubagent`) and retry, not to change the call's arguments.
Its boolean sibling `concurrency_limits_exceeded()` is what `try_wake_next_queued_agent()` (the
`SendMessage` scheduler) checks instead, since exceeding the limit there means "leave this
message queued," not "raise" — see "Agent-to-agent messaging" below.

`CreateSubagent` also rejects, before constructing any `Session`: exceeding
`subagents_max_depth`, the calling role lacking `allow_subagents: true`, a requested role
outside the caller's own `effective_subagent_roles`, and a requested role whose `max_copies`
is already reached by sessions elsewhere in the tree — `klorb.agents.policy._check_max_copies()`
raises `ToolCallError(category="validation")` naming each existing session's id so the caller can
`SendMessage` it instead of creating a new one.

## Tools

### CreateSubagent

Args: `role`, `session_title`, `initial_message`, `model` (optional override), `allowed_tools`/
`allowed_skills` (optional per-call overrides of the role's own `restrict_to.tools`/`.skills` —
still intersected against the caller's own effective sets, never widening them),
`max_output_tokens` (optional), and `starting_task_id` (optional).

Runs every check in "Subagent lifecycle"/"Security model" above; if all pass, constructs the
child `Session`, calls `Session.ensure_chainlink_client()` on the *creating* session if the new
subagent's own tool set includes a `TASKS` tool (see docs/specs/chainlink-task-tracking.md's
"Setup" section), and calls `klorb.agents.policy.dispatch_subagent_turn()`, which registers the
`SubagentHandle` and starts a daemon `threading.Thread` running the subagent's first turn, then
returns immediately with the subagent's id and a note explaining how its output will be
delivered. The caller is expected to keep working; it must not expose the returned id to the
user, since it has no meaning to them — only to a later `WaitForSubagent`/`SendMessage` call.

`starting_task_id`, if given, pre-claims a chainlink task for the new subagent and incorporates
the task's summary (title and description) into the subagent's first user prompt, so the
subagent knows what it's working on without a separate `TodoCreate`/`TodoNext` call. If the
task carries the `ALL_LABEL` ("all", unclaimed) label, it is claimed for the child by removing
`ALL_LABEL` and adding `agent_label(child.id)` before the subagent starts. Raises
`ToolCallError` (category `"validation"`) if the task doesn't exist, is closed, or is already
claimed by a different agent. The parent performs the claim synchronously so no other agent can
poach the task during subagent startup. See docs/specs/chainlink-task-tracking.md's "Task
assignment" section.

### WaitForSubagent

No arguments. Blocks the calling session's own dispatch thread
(`SubagentTracker.pop_all_completed()`, polling with a short timeout so it can also notice
`cancel_event`) until at least one of the caller's own subagents has finished, then returns
**every** subagent that has finished by that point (not just the first one) as
`{"completed": [{"subagent_id", "role", "title", "output"}, ...]}`, oldest first — so several
subagents that finished before the caller got around to waiting are all delivered in one call.
Fails immediately, without suspending, if the caller currently has no subagents running or
awaiting delivery (`SubagentTracker.has_undelivered()`).

### SendMessage

Args: `id` (any agent's session id, from the `AgentGroup` table — not necessarily the caller's
own subagent), `message`. Requires the caller's own role to have the `send_messages` capability
(`ToolCallError`, category `"validation"`, otherwise). See "Agent-to-agent messaging" below for
the full delivery mechanism; in short, it always enqueues onto the tree-wide `AgentMessageQueue`
first, then runs the same scheduler (`try_wake_next_queued_agent()`) that also runs whenever a
concurrency slot frees up elsewhere, and reports back one of three outcomes: `"delivered"` (the
target was dormant and its next turn is now running), `"busy"` (the target is running, or is a
root session with no active-wake path — the message is queued for `GetMessages`/the standing
interjection), or `"capacity"` (the target was dormant but starting it would have exceeded
`maxConcurrentPerParent`/`maxActiveTotal` — also queued, woken opportunistically once the tree
next frees a slot).

### GetMessages

No arguments. Available to every role regardless of `send_messages` — any agent may receive and
read messages. Pops every entry in the `AgentMessageQueue` addressed to the caller's own session
id (`AgentMessageQueue.pop_all_for()`) and formats them as `"No new messages waiting."` or a
numbered list, each entry tagged `"(parent)"` if its sender is the caller's own `Session.parent`.
For any popped message whose sender *is* the caller's own parent, calls `SubagentTracker.
mark_parent_interested()` on that parent's tracker — the parent is now expecting this session's
eventual output again, even though no new turn was dispatched to produce that effect.

## Direct user messaging

A human using the TUI or vscode-plugin addresses a subagent directly through
`klorb.agents.policy.dispatch_direct_message(process_config, child, handle, message)` — never
through a tool call, so never subject to `SUBAGENT_MGMT_TOOL_NAMES`'s role-based restrictions.
Which of the two branches runs is chosen from `child.parent.subagent_tracker.current_handle(child.
id)`'s `state` — re-resolved fresh rather than trusting the `handle` argument, which may have gone
stale since the caller looked it up — mirroring the same duality the root session's own prompt
input already has:

* **Running**: `message` is enqueued into the live turn (`Session.enqueue_queued_message()`),
  exactly like a human queuing a message into their own root session mid-turn. `parent_interested`
  on the handle is left untouched — that turn was dispatched by whoever started it (ordinarily the
  parent), and a human steering it mid-turn doesn't change who's expecting the outcome.
* **Finished (dormant)**: a fresh turn is dispatched (`dispatch_subagent_turn(...,
  parent_interested=False)`), after the same `check_concurrency_limits()` check `CreateSubagent`
  runs — a human resuming a subagent from a UI consumes a `"running"` slot exactly like a
  tool-driven resume does, and is bound by the same `maxConcurrentPerParent`/`maxActiveTotal`
  limits. Unlike `SendMessage`, this raises rather than queuing on failure — there is no UI-level
  retry-later path for a human's own direct message today.

The subagent itself never distinguishes a message that arrived this way from one its parent sent:
both simply become the next user turn (or mid-turn interjection) in the child `Session`'s own
conversation. Only `parent_interested` differs, and only that flag decides whether the eventual
completion is ever handed to the parent (see "Communicating back to the parent").

The whole read-then-act sequence runs under `child.parent.subagent_tracker.dispatch_guard()`
(distinct from the tracker's own internal bookkeeping lock), closing the window between reading a
dormant subagent's state and dispatching a fresh turn for it — without it, two callers racing for
the same subagent (e.g. a debounced `_klorb/subagentPrompt` double-fire from the ACP server, which
handles requests concurrently unlike the TUI's single-threaded direct-messaging path) could both
see it as free to resume and both call `dispatch_subagent_turn`, leaving one of the two spawned
threads silently dropped from the tracker (`SubagentTracker.register()` replaces the dict entry
for a child id rather than merging into it) and never joined, cancelled, or delivered.
`dispatch_direct_message` returns which branch ran (`"queued"` or `"started"`), resolved inside
the guard rather than guessed beforehand from a `handle.state` read a concurrent call could make
stale.

## Agent-to-agent messaging

`SendMessage`/`GetMessages` let any two agents in the same session tree converse directly,
independent of the creator/subagent relationship `CreateSubagent`/`WaitForSubagent` model —
`klorb.agents.runtime.find_session_in_group()` resolves the target by id anywhere in the
sender's own tree, and `klorb.agents.messaging.AgentMessageQueue` (one instance per tree, shared
by every `Session` in it via `Session.agent_message_queue`, mirroring how `workspace_indexer` is
shared) is where a message sits until it's actually delivered.

* **Delivery is unified through one scheduler.** `SendMessage` never dispatches a turn directly;
  it always calls `AgentMessageQueue.enqueue()` first, then `klorb.agents.policy.
  try_wake_next_queued_agent(process_config, session)`. That same function also runs every time
  `dispatch_subagent_turn`'s worker marks a subagent finished (a concurrency slot just freed) —
  the only two events that can change whether a queued message's target is now dispatchable.
* **The queue is one flat, tree-wide FIFO, not one per recipient**, so that when more dormant,
  message-bearing agents exist than `maxConcurrentPerParent`/`maxActiveTotal` allow to run at
  once, whichever was messaged first is woken first as slots free up — `QueuedAgentMessage
  (sender_id, sender_role, recipient_id, body)`. Capped at `messaging_max_queue_size`
  (`tools.messaging.maxQueueSize`); `enqueue()` raises `ToolCallError(category="transient")`
  outright once full, rather than dropping silently.
* **`try_wake_next_queued_agent()` only actively wakes a *dormant subagent*.** It scans the FIFO
  for the first entry whose recipient has a `parent` and a `SubagentHandle` in `"finished"` state,
  skipping past (without stopping the scan) any entry whose recipient is currently `"running"` or
  is a root session — neither is competing for a concurrency slot, so neither blocks a later
  dormant candidate from being found. The first dormant candidate found is authoritative for
  fairness: if starting it would exceed `concurrency_limits_exceeded()`, the scan **stops there**
  rather than skipping ahead to a later dormant recipient that might currently fit — that's what
  makes waking starvation-free. Otherwise every one of that recipient's queued messages is popped
  and bundled into one `klorb.agents.messaging.format_new_turn_message()` prompt (explaining
  they're from other agents, not the user, and tagging any sender that is the recipient's own
  `parent`), and `dispatch_subagent_turn()` starts its turn with `parent_interested` set to
  whether any popped sender is that parent.
  * **Locking**: to avoid the same stale-read race `dispatch_direct_message` already guards
    against, `AgentMessageQueue`'s own lock is only ever held *alone* for a lock-light peek that
    names a candidate; the actual re-check-and-act (current handle state, `concurrency_limits_
    exceeded()`, popping, dispatching) happens afterward under that one candidate's own
    `SubagentTracker.dispatch_guard()`, discarding the candidate with no side effects if it's no
    longer eligible by then (someone else already delivered to it, or capacity was consumed
    elsewhere) — its messages stay queued for the next trigger. This is the same "`dispatch_guard`
    is always acquired before any queue access, never the other way around" ordering the
    end-of-turn drain below follows, so the two can never deadlock or double-dispatch the same
    recipient.
* **A message to an idle root session is delivered directly**, bypassing the queue entirely:
  `SendMessageTool.apply()` checks `target.current_turn_handlers() is None` for a root target
  and, if so, calls `Session.deliver_event_message()` with the message framed through
  `format_new_turn_message()` — the same host-agnostic wake mechanism `Timer`/
  `FileSystemModified`/`WorkspaceTrustChanged` hook output already uses to start an idle root's
  next turn (`docs/specs/hooks-and-events.md`). A root built with no host attached (`_wake_handler`
  is `None`, no turn in flight — a bare `Session()` in a test or minimal embedding) makes
  `deliver_event_message()` raise `ChainedHookMessageUndeliverableError`; `SendMessageTool`
  catches that and falls through to the ordinary queued path below rather than surfacing a tool
  error to the sender.
* **A message to a *running* target, or to a *busy* root session**, is left in the queue for the
  recipient to discover itself: a standing `<SystemInterjection subject="AgentMessage">`
  (registered on every session in `_reset_state`, the same mechanism as the `AgentGroup`
  interjection) fires whenever `AgentMessageQueue.has_pending(self.id)` is true, reminding the
  session to call `GetMessages`. This is polled at the same two points every other standing
  interjection is: the start of a new turn, and between each round of tool calls within one.
* **`WaitForSubagent` is interrupted by an incoming agent message**, exactly like a queued human
  message already interrupts it: `SendMessage` calls the target session's `Session.
  notify_new_message()` (which sets the same `_user_msg_event` `enqueue_queued_message()` sets)
  after enqueueing, and `WaitForSubagentTool`'s pre-loop check now also tests `AgentMessageQueue.
  has_pending(session.id)` (alongside the existing `pending_queued_message_texts` check) to catch
  a message that arrived — and already cleared the event — before the wait even started.

## Communicating back to the parent

`klorb.agents.policy._run_subagent_turn()` is the background thread's top-level call: it runs
one turn of the child's conversation and returns the text to deliver, concatenating every
`role="assistant"`/`"tool_use"` message's `content` produced during the turn, in order
(`_assistant_authored_text`) — a subagent's turn may emit commentary alongside one or more
tool-call rounds before its final plain-text reply (only the very last message can be plain
`"assistant"`; `Session._dispatch_turn` loops only while a reply's role is `"tool_use"`), and an
earlier `"tool_use"`-role reply can itself carry non-empty commentary alongside the tool calls it
requested — using all of it keeps that commentary from being silently discarded. A turn that
said nothing at all becomes "The subagent completed its work without saying anything."; one
aborted mid-stream (`ResponseAborted`, from `cancel_event` firing) appends
"(Subagent turn aborted by user)"; one whose turn raised becomes
"(Subagent turn failed: `<exception>`)". This function never itself raises — it's the background
thread's own top-level call, and an unhandled exception there would silently strand the subagent
as `"running"` forever.

The background thread's own drain-and-finish decision — drain the child's own local queue, then
its entries in the tree-wide `AgentMessageQueue`, and finish only if both are empty — runs under
`parent.subagent_tracker.dispatch_guard()`, the same lock every enqueue-vs-dispatch decision for
that child takes (see "Agent-to-agent messaging" above). A message enqueued concurrently, from
either source, is therefore either drained into one more turn or lands after the handle is
already `"finished"` and resumes the subagent through the ordinary dormant path; it can never
strand on a finished subagent that nothing will drain again. Draining a non-empty
`AgentMessageQueue` batch also calls `SubagentTracker.mark_parent_interested()` if any of it came
from `parent`, for the same reason `GetMessages` does (see "Agent-to-agent messaging"). Once
either drain empties out for good, `try_wake_next_queued_agent()` runs once more (outside the
guard) before the thread actually exits, so a slot this subagent just freed is immediately
offered to whichever other dormant, message-bearing agent has been waiting longest.

Once a turn ends, `SubagentTracker.mark_finished()` sets `state`/`output` on the handle, then —
only if the handle is `parent_interested` — queues it for delivery via `WaitForSubagent`,
described above. An uninterested completion (a human addressed this subagent directly, see
"Direct user messaging") still gets `state`/`output` set, so it stays visible to the subagents
panel and a later `SendMessage`, but is never queued: nothing pops it, and it is never handed
to the parent.

Right after `mark_finished()` (both call sites in `dispatch_subagent_turn`'s worker, always
outside `dispatch_guard()` — see "Locking" above), `klorb.agents.policy.
_relay_completion_to_parent()` tries to hand the output straight to the parent instead of leaving
it for an explicit `WaitForSubagent` poll. It first calls `SubagentTracker.try_claim_for_relay()`
to atomically check-and-mark the handle delivered, so a `WaitForSubagent` call racing on the
parent's own thread can never also deliver the same completion — the two paths share the
tracker's own `_lock`, and `pop_next_completed()`/`try_pop_completed()` both skip an
already-delivered handle rather than returning it a second time. If the claim succeeds:

* **`parent` already has a turn of its own in flight** (`current_turn_handlers() is not None`,
  root or subagent): the claim is released and nothing further happens. `mark_finished()`'s
  completion-queue push already covers this case for `WaitForSubagent`, and routing it through
  `AgentMessageQueue` too would leave a self-addressed entry with no natural consumer other than
  `GetMessages` — which nothing would ever think to call for it — spuriously interrupting that
  same `WaitForSubagent` call, or a later one, with a "new message" that's actually just the
  completion it's already about to return.
* **`parent` is an idle root**: delivered the same way `SendMessage` delivers to an idle root (see
  "Agent-to-agent messaging"), via `deliver_or_queue_agent_message()`. A root with no host
  attached to wake (`ChainedHookMessageUndeliverableError`) releases the claim instead, leaving
  the completion queue as the only delivery path, exactly as it already was.
* **`parent` is a dormant subagent**: also routed through `deliver_or_queue_agent_message()`,
  which actively wakes it via `try_wake_next_queued_agent()` — fixing what would otherwise be a
  silent drop under `maxConcurrentPerParent`/`maxActiveTotal` contention, the same FIFO-fair
  behavior a genuine `SendMessage` gets. If capacity is exceeded, the claim is released and the
  message stays queued in `AgentMessageQueue`, discoverable once capacity frees.

`klorb.agents.runtime.build_subagent_interjection_provider()` builds an equivalent
zero-arg provider closure, in the same shape `Session.register_standing_interjection()` expects
(the mechanism `BashTool`'s persistent-shell notice uses) — wrapping a popped completion's body in
`<SystemInterjection subject="subagent">id: ...\nrole: ...\ntitle: ...\n\n<output></SystemInterjection>`.
No production tool call site registers it today; `WaitForSubagent` is the only channel that
currently delivers a subagent's output to its creator. This `subject="subagent"` interjection is
distinct from the `subject="AgentMessage"` one "Agent-to-agent messaging" describes above — the
former is a *creator* collecting its own subagent's completed output, the latter is any agent
being reminded to `GetMessages` from any other agent.

## Persistence

Subagent sessions are never separately persisted to their own `sessions/<subdir>/` — a
subagent's entire durable contribution is whatever relay text lands in its creator's own
persisted `messages` via `WaitForSubagent`. Restarting klorb does not resume an in-flight
subagent turn; there is no checkpoint to resume from.

`klorb.agents.runtime.cascade_close_subagents()` runs at the start of every `Session.close()`
(before `_finalize_session_persistence()`, so its relay lands in `messages` before `session.json`
is written), recursing deepest-first: for each of `session`'s subagents, first close *its own*
subagents, then — if it's still `"running"`, signal `cancel_event` and join its thread (up to a
short timeout; a wedged subagent must not hang process shutdown) — and if it is `parent_interested`
and its output was never delivered, append it directly into `session.messages` as a `role="user"`
message wrapping the same `<SystemInterjection subject="subagent">` body, with a trailing note
that the user provided no prompt this turn. An uninterested subagent (a human addressed it
directly, see "Direct user messaging") is still recursively closed but never force-appended this
way — its final output was never the parent's to receive. A subagent whose thread never finished
within the shutdown timeout gets "(Subagent terminated: harness closed before it finished)" in
place of real output. This only runs on a graceful shutdown (quit); a crash loses in-flight
subagent work exactly as it loses any other in-flight session state.

## Explorer role

`resources/system_prompts.d/roles/explorer/default.md` instructs the Explorer to explore
whatever it needs to (files, prior decisions, memories, web pages) to answer a bounded question,
never to modify the codebase or environment, and to end its turn with a report meant to stand on
its own — that report is its only deliverable; the creator never sees its intermediate tool
calls or reasoning. Its `agents.json` entry: `enforce_readonly_tools: true`, `skills:`
`["internal:launch-explorer-subagent"]` (the one skill it may use, so it can delegate
sub-questions to its own Explorer subagents), and a `tools` list covering `FindFile`, `Grep`, `ListDir`, `ReadFile`, `ListMemories`,
`SearchMemories`, `ReadMemory`, `EditScratchpad`, `ReadScratchpad`, and `WebFetch`.
`restrict_to.subagent_roles` names `["explorer"]` and `allow_subagents: true`, so an Explorer may
itself launch further Explorer subagents (bounded by `tools.subagents.maxDepth` like any other
nesting) — operator's own `restrict_to.subagent_roles` names `["explorer", "reviewer"]`, so
neither role can ever launch an `operator` subagent (see "Security model" above).

## Reviewer role

`resources/system_prompts.d/roles/reviewer/default.md` instructs the Reviewer to audit a
completed change (via the `internal:code-review` skill) and report back to whoever launched it,
never to keep developing the change itself. Unlike Explorer, its `agents.json` entry sets no
`restrict_to.tools`/`.skills` at all and `enforce_readonly_tools` is left at its `false` default
— a Reviewer inherits its creator's full tool set, including `Bash` and the file-edit tools, so
it can reproduce a suspected bug (a throwaway script, a temporary probe edit, an actual test run)
rather than only reading code and guessing. It's still expected to leave the working tree exactly
as it found it — that discipline lives in the role's prompt and the `code-review` skill, not in a
tool restriction, since confirming a bug sometimes requires the same tools that would let it fix
one. See [[grant-reviewer-full-tools-not-enforce-readonly]] for why this deliberately diverges
from Explorer's `enforce_readonly_tools: true` pattern.

`restrict_to.subagent_roles` names `["explorer"]` and `allow_subagents: true`, so a Reviewer may
launch Explorer subagents to read a diff and its surrounding code in parallel. Its
`agent_capabilities` are `accepts_tasks: false` (it never holds a tracked task of its own — a
review is a bounded request/response, not ongoing work), `assigns_tasks: true` (it may file
follow-up work it finds but decides is out of scope, assigned to the parent or another agent in
the group), and `see_group_tasks: true` (it may see everything the group is tracking, for
context on what the change was supposed to accomplish).

## Implementer role

`resources/system_prompts.d/roles/implementer/default.md` instructs the Implementer to carry
out a plan its creator (or a Planner) already wrote, running the same
research/decide/execute/verify/report loop as `operator`'s own default prompt but without any
of Operator's task-ownership, delegation, or problem-decomposition responsibilities — it never
scopes, plans, or reviews another agent's work, and its report is expected to flag a plan that
turns out to be wrong rather than silently reinterpreting it.

Like Reviewer, its `agents.json` entry sets no `restrict_to.tools`/`.skills` at all and
`enforce_readonly_tools` is left at its `false` default — it inherits its creator's full tool
set, since carrying out a plan requires the same file-edit and `Bash` access as any other
implementation work. `restrict_to.subagent_roles` names only `["explorer"]` and
`allow_subagents: true`, so an Implementer may launch Explorer subagents for research but can
never launch a Planner, Reviewer, or another Implementer — implementation is not something it
delegates onward. Its `agent_capabilities` are `accepts_tasks: true` (it may hold a group task,
or one of its own, as its current tracked task), `assigns_tasks: false` (`TodoCreate` may only
target itself; it cannot file work for another agent), and `see_group_tasks: true` (it may see
everything the group is tracking, for context on how its slice of the plan fits the rest).

## Pair Programmer role

A Pair Programmer is not a bounded one-shot specialist like the roles above -- it's an ongoing
collaborator an Operator spawns via the `internal:pair-programming` skill to work through a large
or architecturally uncertain task together, staying in touch over `SendMessage` for the rest of
the session. Its `agents.json` entry sets no `restrict_to.tools`/`.skills` at all (it inherits its
creator's full tool set, the same as Reviewer/Implementer), `restrict_to.subagent_roles` names
`["explorer"]`, and `allow_subagents: true` (it may delegate research to Explorer subagents, but
never launch another Pair Programmer or an Operator). Its `agent_capabilities` are `accepts_tasks:
false` (it never holds a tracked task of its own), `assigns_tasks: true` and `see_group_tasks:
true` (it may review and add to the Operator's todo list), and `send_messages: true`.

Its actual working instructions live in `resources/system_prompts.d/roles/pair_programmer/
default.md`, active from its very first turn like any other role's system prompt. Its live file
watch is likewise a property of the role itself: `pair_programmer`'s own `agents.json` entry
carries an `events.FileSystemModified` grant (see "Role-granted hooks/events" below), so
`klorb.agents.policy.plan_subagent_creation` folds it onto every subagent it creates as this role
and starts its watcher right after construction -- no skill activation needed to arm it. `watch:
"."` with `applyGitignore: true` (docs/specs/hooks-and-events.md's `FileSystemModified` section)
keeps it quiet through `.chainlink`/build-artifact churn.

### Role-granted hooks/events

An `agents.json` role entry may carry its own `hooks`/`events` fields, the same
`{name: [handler, ...]}` shape a skill's `metadata.klorb.hooks`/`.events` frontmatter grant
carries (docs/specs/skills.md's `metadata.klorb.hooks`/`.events` section) -- `klorb.agents.
definition.agent_hook_configs`/`agent_event_configs` parse them via the same
`klorb.hooks.merge.parse_session_scoped_hook_dict`/`parse_event_dict` helpers a skill grant uses,
rejecting a process-scoped hook name and defaulting `isHeritable` to `false` the same way. Unlike
a skill grant, a role's own hooks/events apply unconditionally to every subagent
`plan_subagent_creation` builds for that role -- folded onto whatever the child already inherits
from its creator's own heritable `hooks`/`events`, via the same named-list-concatenate merge
(docs/specs/hooks-and-events.md's "Merge behavior" section) every other layer uses. Because a
subagent never fires its own `onSessionStart` (the moment that would otherwise start a
`FileSystemWatcher`/`TimerScheduler` for `config.events`), `CreateSubagentTool.apply()` starts one
explicitly for just the role's newly-granted event entries (`SubagentPlan.role_events`), right
after constructing the child `Session`. This mechanism is scoped to subagent creation only -- a
root session running directly as a role with its own `hooks`/`events` grant does not pick them up,
since root sessions are constructed through a separate path (`compute_root_session_grants`) that
doesn't thread this through.

## Subagents panel (TUI)

`klorb.tui.widgets.subagents_panel.SubagentsPanel` (Ctrl+G, `klorb.tui.mixins.subagents_panel.
SubagentsPanelMixin`) docks a right-hand panel — mutually exclusive with the Ctrl+T task
sidebar, since both occupy the same slot — listing every session in the live tree rooted at the
process's top-level session (`klorb.agents.runtime.walk_session_tree`, a pre-order walk mirroring
`total_active_subagents`/`cascade_close_subagents`'s own recursive walk). Each row shows the
session's address and title; the footer shows the selected row's `config.role_name`. The inner
`OptionList` handles click/up-down-arrow navigation itself; highlighting a row calls
`SubagentsPanelMixin._select_session`.

**Selection is global, and gates every ask — root included.** `ReplApp._selected_session`
(default: the root session) and `_selected_handle` (`None` for the root) track which session is
currently displayed. `InteractionsMixin._confirm_permission_ask`/`_confirm_ask_user_questions`/
`_confirm_escalate_privileges` each mount their panel inside `InteractionsMixin.
_reserved_interaction_slot(session_id)`, an async context manager that loops calling
`SubagentsPanelMixin._await_session_selected(session_id)` (which polls via `asyncio.sleep`,
mirroring `SubagentTracker`'s own poll pattern rather than a new synchronization primitive) and
then acquiring `_interaction_lock`, re-checking that `session_id` is *still* selected once the
lock is actually granted — if the user switched to a different session while queued behind
another panel (or behind a classifier round trip in `_confirm_permission_ask`'s case), it releases
the lock and waits again rather than mounting for a session that's no longer on screen. This makes
"the ask only shows once its owner is selected, and stays hidden the instant it stops being
selected" the general rule (a strict generalization of the pre-Phase-3 behavior, which was
equivalent to "root is always selected"), not a subagent-specific special case, and keeps a queued
ask from ever disabling the prompt input out from under an unrelated session's transcript. While
waiting, the session's id sits in `_attention_needed` (an insertion-ordered `dict[str, None]`),
which `SubagentsPanel.show_rows` renders as a blinking `(!)` marker (`_tick_subagents_panel`, a
`set_interval` timer, flips the blink phase every 0.6s) and which drives the
`#subagent-attention-status` status-line fallback ("Agent 1.1 needs your input") shown only while
the panel itself is hidden.

**The transcript view is a second, separate `#subagent-history` `VerticalScroll`**, not a
repurposed `#history` — the root session's own live streaming into `#history` is completely
unaffected by panel selection. Selecting a subagent renders a fresh snapshot of its
`Session.messages` into `#subagent-history` (`SubagentsPanelMixin._render_full_subagent_transcript`),
reusing `RenderingMixin`'s pure `_render_restored_tool_call`/`_render_tool_result` against the
*root* session's own `tool_registry` — safe because a subagent's tool set is always a subset of
every ancestor's, including the root's, by the Phase-1 intersection invariant. While it stays
selected, the tick timer catches the view up incrementally (`_append_new_subagent_messages`, mounting
only messages added since the last render, mirroring how `#history` streams a live turn) rather than
rebuilding it from scratch. `Session.messages` returns a fresh copy on every access, so both methods
read it exactly once into a local variable and reuse that same snapshot for the mount and for the
rendered-count bookkeeping that follows — a second, later read could observe a longer list than what
was actually mounted (the subagent's own background turn thread keeps appending to it concurrently),
silently stranding whatever arrived in between as never-rendered. It follows the bottom only if the
view was already pinned there
(`_subagent_history_pinned_to_bottom`, kept in sync by `_on_subagent_history_scroll_changed` the same
way `_history_pinned_to_bottom` is) — a user who scrolled up to reread earlier output isn't yanked
back down by new content. A trailing status `Static` (`_mount_subagent_status_notice`) always
occupies the last line: "Subagent is still working…" while `handle.state == "running"`, else
"Subagent task complete." — or "Subagent interrupted." instead, if `handle.output` carries
`klorb.agents.runtime.SUBAGENT_ABORTED_MARKER`. The "still working" notice is a
`CrawlAnimatedStatic` (`klorb.tui.widgets.tool_call_widgets`), the same crawling highlight
animation `RunningToolCallStatic`'s "Running..." uses, so it stays visibly distinct from a frozen
UI.

**Escape/Ctrl+C** abort the selected subagent's own `cancel_event` when one is selected, leaving
the root session's `_cancel_event`/`_shell_cancel_event` untouched — the existing
`SUBAGENT_ABORTED_MARKER` relay (`klorb.agents.policy._run_subagent_turn`) already covers the
resulting `ResponseAborted`. Since the subagent's background thread only notices `cancel_event` at
its next stream/tool-call boundary, `KeyActionsMixin._interrupt_running_activity` also calls
`SubagentsPanelMixin._note_subagent_interrupt_requested` to immediately show "Sending interrupt…"
in `#subagent-history` — tracked via `_subagent_interrupt_pending` so every tick in between keeps
showing it rather than reverting to "still working" — until the handle actually finishes and the
notice flips to "Subagent interrupted.". **The prompt input stays enabled for any selection, root
or subagent alike** (`SubagentsPanelMixin._update_prompt_input_disabled_state`; only an open
permission/ask panel's own `interaction-active` class disables it) — submitting while a subagent is
selected routes the message to it directly via `dispatch_direct_message` instead of the root
session (`PromptSubmissionMixin._submit_subagent_prompt`, see docs/specs/subagents.md's "Direct
user messaging" section). Unsubmitted draft text is tracked per session id in
`ReplApp._subagent_drafts`, saved and restored around every selection change
(`SubagentsPanelMixin._select_session`) so switching away and back doesn't lose it. **The footer
token tallies** (`StatusBarMixin._update_status_bar`) also follow the
selection — they report `_selected_session.total_tokens_used()`/`max_context_window()`/
`total_output_tokens_used()`, not always the root's, so they read correctly for whichever
transcript is actually on screen.

**Every other selection-dependent chrome follows the same rule.** The `Header`'s title (model
name, plus thinking effort in parentheses if enabled) reads `_selected_session.config` directly
in `ReplApp.format_title` — a subagent can run a different model than the root, entirely
independently, since `SessionConfig` is deep-copied at subagent creation. `_select_session` calls
`_refresh_header_title()` (the same `mutate_reactive(sub_title)` trick already used for
thinking-effort/workspace-trust changes) so the header redraws even though `self.sub_title`'s own
*value* — which still only ever tracks the root's model, via `select_model` — didn't necessarily
change. The `#session-name` status line (`"Session: <title>"`) is handled the same way
(`SubagentsPanelMixin._update_session_name_line_for_selection`, reading `_selected_session.name`);
`PromptSubmissionMixin._handle_session_name_changed` (the root session's own first-turn naming
classifier callback) now only actually writes to that line while the root is the one currently
selected, so a subagent's title on screen can't be clobbered by the root's classifier resolving
in the background.

**A subagent's transcript must show everything a live turn would, not just tool calls.** Two
gaps existed in the first cut of `_mount_subagent_messages` (and, identically, in `RenderingMixin.
_mount_restored_history`, since both walk a `Session.messages` list the same way once a turn is
over rather than reacting to `TurnEventHandlers` streaming callbacks): a `role="tool_use"`
message's own `content` was never rendered, only its `tool_calls` — but `Session._send_and_receive`
sets `content` before reclassifying a message to `tool_use` purely because it also carries tool
calls, so a round that ends with both commentary *and* tool calls (including, often, a subagent's
actual final answer, if that answer arrived in the same round as its last tool calls rather than a
trailing text-only round) had its text silently dropped — exactly the fact `klorb.agents.policy.
_assistant_authored_text` already accounts for when building the text relayed to the creator. Both
render paths now mount a `tool_use` message's non-empty `content` as a response block ahead of its
tool calls. Separately, a `"thinking"` message's `content` (the model's plain-text `reasoning`
delta) and its `reasoning_details` (structured fragments) are populated from two independent
provider streams that aren't guaranteed to be in sync — a `<Thinking>` block could render as an
empty label if `content` never arrived even though real text existed in `reasoning_details`. Both
render paths now call `klorb.tui.formatting.resolve_thinking_body_text(content, reasoning_details)`
before mounting the `<Thinking>` body, which reconstructs the text from `reasoning_details`'
`text`/`summary` fields whenever `content` is empty. (The live-streaming render path,
`PromptSubmissionMixin.handle_thinking_chunk`/`handle_reasoning_details_chunk`, has the same latent
gap but isn't covered by this fix — mid-stream reconciliation is a materially different, racier
problem, and a reload/restore of the same session renders correctly either way.)

## Out of scope

* **`VisionAssistant` role** and any other specialist role beyond Explorer and Reviewer.
* **`@mention` filtering by the creator's `readDirs`.** Today a `@mention` in a message sent to
  a subagent is left entirely unresolved (see "Security model"); actually resolving it while
  still applying the creator's own `readDirs` rules is unbuilt.
