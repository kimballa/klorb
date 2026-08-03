# Plan 021: Subagent support

This will be a large project and I expect multiple agents to work on chunks of it in succession. The
plan should be staged to allow for this incremental building.

## Setup and config files / vars

need a processConfig var for max agent recursion depth (tools.subagents.maxDepth) default value 2
(this means two levels below the main agent talking to the user; so there can be the user's agent,
their child and a grandchild; no deeper).

need two more processConfig vars to bound subagent *breadth*, since maxDepth alone doesn't stop a
parent (whether from a bug or from prompt injection steering it off course) from creating many
subagents and blowing through cost/resource limits:

* `tools.subagents.maxConcurrentPerParent` (default value 4) - the most subagents any single agent
  may have running (not yet finished-and-delivered) at once.
* `tools.subagents.maxActiveTotal` (default value 16) - the most subagents that may be
  simultaneously active across an entire session tree (rooted at the top-level session), regardless
  of which agent created them.

`CreateSubagent` must reject a call that would exceed either limit, the same way it rejects a call
that would exceed `maxDepth`.

need a system prompt to go in system_prompts.d/roles/explorer/default.md for the Explorer subagent,
which is our first agent prototype.

* Instructs the subagent to explore the codebase to answer a question
* Tells the subagent not to modify anything
* Its job is to produce a report as its main output, which will be relayed to the agent that asked
  it to do the exploring.
* It may also use the scratchpad, which is shared with the main agent.

in klorb/resources/, add an agents.json file. This file has schema envelope `'klorb-agents'`.
The purpose of the file is to define restrictions on the subagent capabilities

```json
{
  "agents": [
    {
      "name": "explorer",
      "default_model": "some/model",
      "restrict_to": {
        "tools": [ "ToolName1", "ToolName2", "ToolName3", ... ],
        "tool_categories": [ "Category1", "Category2", ... ],
        "skills": [ "internal:someSkill", "internal:anotherSkill", ... ],
        "subagent_roles": [ "some_role", "another_role", ...],
        "enforce_readonly_tools": true/false
      },
      "allow_subagents": True/False
    },
    ...
  ]
}
```

* `agents` gives us an array of AgentDefinition BaseModel objects.
* The allow_subagents field tells us whether this agent role is allowed to have subagents of its
  own, regardless of nesting depth.
* The restrict_to field produces an AgentRestrictions BaseModel. The purpose of this is to filter
  down the tools and skills that a subagent has access to. This field / both of its attrs tools and
  skills, are optional. If left unspecified, the subagent will inherit all the tools and/or skills
  of its parent.
* If tools or skills are the empty list, that is *not* unspecified; that means *no* tools/skills are
  inherited.
* in addition / instead, the `tool_categories` field can be used to restrict tools by taking the list
  of tools that the parent agent has available to it, and filtering that list to retain only those
  whose `tool.category()` returned one of the values in the `tool_categories` list. This allows us
  to rule in/out entire categories of tools, which may encompass tools that don't currently exist,
  without naming each individual tool to accept.
* in addition / instead, the enforce_readonly field, if true, will clamp tool use to only the subset
  of the permitted tools that advertise themselves as read-only. (This is most usefully set to true
  when 'tools' itself is left unspecified.
* restrict_to.subagent_roles clamps down the roles allowed for subagents this subagent can itself
  recursively launch.

When the subagent is created, the parent may specify their choice of model to use. But if left
unset, default_model will be used.

`agents.json` is parsed once, at process start, into an immutable in-memory registry. A running
process never re-reads it, so editing the file on disk cannot retroactively loosen the
restrictions already computed for sessions that are live in that process.

## Addressing

The initial session has an "address" of 1.

Subagents are given numeric ids starting at 1, that are 'dotted-decimal' extensions. i.e., the
subagents of session 1 are 1.1, 1.2, 1.3...

The subagents of 1.2 are 1.2.1, 1.2.2, 1.2.3, ...

Addresses are assigned monotonically per parent and are never reused, even after the
corresponding child session finishes or is torn down. This keeps an address a stable reference to
one specific subagent session for as long as the process (and any history/logs referencing that
address) exists.

An address is a purely human-facing display label for where an agent sits in the tree — it is not
a `Session.id` and no tool operates on it (`CreateSubagent`/`WaitForSubagent`/`MessageSubagent` all
take real session ids). It's derived from the `parent.id` links each `Session` carries (see
"Subagent session model"), maintained by whatever in-memory structure tracks the live tree of
running sessions for a process — the same tracking a cascading teardown (see "Lifecycle and
teardown") needs anyway to find a session's descendants. Addresses are not persisted; they're
recomputed from the live tree whenever the panel renders them.

## Visualization for the user

subagents will be visible in a panel

* in vscode, it's docked at the top, just above tasks
* in tui it's docked on the rhs, and either tasks or subagents are visible at once

Each row shows the agent address (e.g. "1.1") and the title.

The panel also includes a row at the bottom which displays the role of the currently-selected/active
agent.

The user can click into the panel to focus the panel, and can focus different (sub)agents by
clicking on them, or using up/down arrow keys.

In both TUI and VSCode, the HistoryView updates to show the history associated w/ the selected agent
session, and any pending user interaction panel is shown (see "Permissions")

## Security model and AI safety

The subagent must not be able to do anything the parent agent cannot.

### Tool and skill limits

From a skills and tools perspective, if the parent has access to tools A, B, and C, the subagent
cannot have access to tool D. a subagent either inherits all the tools and skills of its parent, or
it inherits a subset of them. The subset is chosen by taking the intersection of the parent's
tools/skills catalog with the specified lists. There is no mechanism for the parent to "widen" the
toolset offered to a child.

**This intersection must always be computed against the parent session's live, already-filtered
effective tool/skill/subagent_role sets — never re-derived fresh from `agents.json` using only the
parent's role name.** A session's effective sets are already the accumulated intersection of every
restriction applied since the root session, including any narrowing a grandparent applied via its
own `allowed_tools`/`allowed_skills` args. If an implementation instead re-looked-up the nominal
`agents.json` entry for the parent's role at each `CreateSubagent` call, a multi-level chain could
recover privileges that an intervening level deliberately stripped, defeating the "no widening"
guarantee across more than one hop. `restrict_to.subagent_roles` is subject to the same rule: the
roles a subagent may itself launch are the intersection with the roles *it* was granted, not a
fresh lookup of what its role nominally allows.

`CreateSubagent`, `WaitForSubagent`, and `MessageSubagent` should report `is_read_only() == True` —
they don't mutate any file or environment state directly. A subagent's actual capabilities are
bounded by the recursive intersection rule above, not by `enforce_readonly_tools`; classifying
these three tools as non-read-only would strip subagent-spawning ability from any
`enforce_readonly_tools: true` role (e.g. Explorer, which is otherwise read-only-enforced yet must
retain the ability to launch further Explorer/Vision subagents per the "Explorer Subagent"
section below).

The `EditScrachpad` tool (which currently reports `is_read_only() == False`) should be modified
to return `is_read_only() => True`. It must not be silently striped from read-only-enforced state.
It does write to disk, but the scratchpad is harness-managed shared workspace state, not the user's
own files or environment — it already sits outside the `writeDirs` permission system for this same
reason (see `EditScratchpad`'s own docstring) — so a role instruction like Explorer's "don't modify
anything" is about the codebase and the user's files, not about this designated collaboration
surface. Concretely: the `EditScratchpad` tool should be modified to report `is_read_only() ->
True`.

Sharing the scratchpad across a subagent tree does introduce one channel that "the subagent simply
says its output out loud" doesn't: a sibling subagent can read what another sibling wrote to the
scratchpad directly, without that content ever passing through the parent's own turn. A poisoned
scratchpad entry (e.g. planted from content read via `WebFetch`) could reach a sibling that never
itself touched the untrusted source, and — since subagent sessions aren't persisted (see
"Persistence") — that exchange might leave no durable trace at all. This is accepted as a residual
risk rather than a reason to withhold scratchpad access: the blast radius still stays inside the
current session tree (it never reaches another conversation, project, or user), which is the same
bound `MessageFragment`/report-relay already operates under with no filter in between, and is the
dimension that distinguishes this from memory poisoning (see "Explorer Subagent" section below).

### File @mentions

When the user references a file with `@filename`, the contents of the file are put into an attachment
to the user message. We don't do a permission check on readDirs, because this is an overt act by the
user, which inherently grants the agent permission to read the file. If we use the session logic as-is,
then this creates a risk that a parent can spawn a subagent and say "read @forbidden-file.txt and tell
me what's inside." Therefore, we must apply readDirs filtering (applying the *parent's* readDirs set)
to any @mentioned files in the prompts passed to the subagent. (This can be a later increment. The
first version of this can simply skip processing of @mentions, leaving them just as `@some-filename.txt`
in the message body without actually appending the file contents as a MessageFragment at all.)

This filtering applies to every parent-to-child message, not only `CreateSubagent`'s
`initial_message` — any later `MessageSubagent` call is an equally overt act by the *parent agent*
rather than the human user, and must go through the same parent-readDirs filter (or the same
skip-@mention-processing fallback) before reaching the child.

### Model/provider visibility

A role's `default_model` (or the parent's override at `CreateSubagent` time) may point at a
different model, and therefore a different inference provider, than the one the user chose for
their primary conversation — e.g. VisionAssistant's default is `xiaomi/mimo-v2.5`, regardless of
what the root session is using. This is allowed by design (it's the whole point of letting a role
pick a cheaper/more-specialized model), but it does mean data the parent hands to a subagent can
flow to a provider the user never separately approved for this conversation. The agents panel
already shows each subagent's role (see "Visualization for the user"), and the role's model choice
is visible from `agents.json`; no additional runtime gate is required, but logging should make it
easy to see after the fact which provider actually handled a given subagent's turn.

### Permissions

Subagents can do things that require user permission.

If we are in an interactive session, that can cause them to put interactive permission panels,
AskUserQuestion panels, etc. up for interaction. Such panels only show when the subagent session is
the selected session (selected in the agents panel)

The subagent begins with its session config as a clone of the parent's session config, so any
readDirs, writeDirs, etc. permissions held by the parent are inherited by the child.

### Shared permission framework state

`permission_framework` is the one exception to "clone and then diverge independently."  It's a live
UI toggle (Shift+Tab in the TUI, `session/set_mode` over ACP) that the user expects to apply to
*everything* running right now, not just whichever session happens to be selected. If each session
in a tree held its own independent copy, a user who flips the top-level session to `deny` would
have no way to stop an already-running subagent that still holds a stale `ask`/`auto` copy — the
toggle would silently fail to do what the user just asked it to do.

So `permission_framework` must be a single piece of state shared *by reference* across an entire
session tree (everything sharing one `root_id`), not copied per-`SessionConfig`: every session in
the tree reads and writes the same shared object, and the existing single mutation entry point
(`Session.set_permission_framework()`) changes it for the whole tree at once. This mirrors how
`ProcessConfig` is already shared by reference across every `Session` in a process, except scoped
to one session tree rather than the whole process — a process may eventually run multiple
independent top-level session trees at once, and each must keep cycling its own permission
framework independently of the others.

`approved_scopes` is deliberately *not* given this same shared-by-reference treatment: a subagent
still only gets the scopes the parent had already approved *at the moment it was created* (a
one-time clone, as above), and a scope approved later in one session does not retroactively
propagate to siblings or already-running relatives. This just extends today's single-session
behavior (an approval lasts "for the rest of the session") to a per-session rather than per-tree
scope, and it's a safe default to under-inherit here — at worst a subagent re-prompts for something
already approved elsewhere, it never gains something it shouldn't have.

There should be a little slowly-blinking `(!)` or something in the agents panel next to an agent
that needs user attention. If the agents panel is closed, a status line should say "Agent <1.1>
needs your input". In both the TUI and VSCode, this can be a single-row "panel" that attaches just
above the prompt input.

## Prompt input

* The prompt input is disabled when a subagent is selected. The user cannot communicate with subagents directly.
* If the subagent is running, the 'stop' button is enabled, and the user can use it to abort the subagent process.
  * If the user does so, then the message output up through that moment is what we communicate back to the parent,
    along with the message "(Subagent turn aborted by user)".

## New tools

### CreateSubagent

Instantiates a new subagent with a specified role and "user message" (from the parent agent).

Args:

* role - explorer, vision_assistant, etc.
* session_title - the title the parent wants to give to the child session (shown to the user; not
  directly to the subagent). This pre-sets the new `Session`'s name at construction time, which
  means the subagent never goes through the one-shot session-naming classifier that runs for a
  freshly-constructed top-level session (that classifier only fires when a session is constructed
  with no name at all). The subagent's `id` therefore keeps its original timestamp + coolname slug
  (e.g. "happy-otter") rather than being renamed to an LLM-derived slug — only its display `name`
  comes from the parent-supplied title.
* initial_message - the message from the parent agent with instructions, etc.
* model - override the default model, if desired by the parent agent. may be null / empty str to use
  default for the role.
* allowed_tools - if specified, overrides the default restrict_to.tools; cannot add tools, still an
  intersection w/ the parent agent's tool set
* allowed_skills - same, for skills.
* max_output_tokens - output token budget the parent wants to give to the subagent. includes
  thinking tokens.

This should actually get the subagent session kicked off and get the message accepted by the subagent model.
If that fails, then the tool call fails, and the error message should be conveyed back to the parent.
The subagent session should be torn down in that case.

Before doing any of that, the tool must reject the call (no session created) if any check fails:

* `parent.depth + 1` would exceed `tools.subagents.maxDepth`.
* the calling agent's role does not have `allow_subagents: true`.
* the calling agent already has `tools.subagents.maxConcurrentPerParent` subagents running.
* the session tree already has `tools.subagents.maxActiveTotal` subagents active in total.

The `allow_subagents` check is best enforced by simply never including `CreateSubagent` (nor
`WaitForSubagent`/`MessageSubagent`) in the tool catalog offered to a role for which it is false,
rather than offering the tool and failing at call time. This is applied as a separate filter on
the tool list received by the subagent, independent of other restrict_to fields.

After the subagent is up and running, the tool returns the id of the subagent and a note that
promises that if the subagent finishes first, its message output will be forwarded to the parent at
the next available opportunity, and if the parent finishes first it should use WaitForSubagent to
wait. Tell the parent not to expose the id to the user as it is meaningless to them, just use it for
future WaitForSubagent or MessageSubagent calls.

**Future work:** add an `assigned_task_id` field that allows the parent to start the subagent off with a
task. This requires us reasoning through how to handle not auto-assigning too many other tasks into
a subagent that shouldn't actually be chewing through the entire todo list (as it may not be
equipped to do so). Now that our todo mgmt tools greedily shove new tasks onto an agent as soon as
it closes its last task, that requires that we rethink those tool I/O patterns a bit.

### WaitForSubagent

Wits for the subagent's turn to end.

The subagent's conversational output is provided as the response body. (the subagent's thinking is
not returned to the parent.) If the subagent ends its turn without saying anything, the body is
simply "The subagent completed its work without saying anything."

Args:

* none

The parent is suspended, and wakes up when any subagent completes its turn and has something to say,
which will be done as the tool response from this tool call.

If the parent has no outstanding (running or already-finished-but-undelivered) subagents at all,
the call fails immediately with an error rather than suspending forever. If more than one subagent
completes before the parent is next available to receive output, completions are queued and
delivered one at a time, oldest-completion-first (FIFO), whether via a subsequent
`WaitForSubagent` call or via the `SystemInterjection` relay described below.

### MessageSubagent

Sends a message to the subagent.

Args:

* id - id of subagent to contact
* message - the message to send to the conversation

The subagent must be in the "finished" state for this to work; otherwise it returns an error saying
the subagent is not done yet. The return error should tell the parent to WaitForSubagent to wait for
it to finish first.

## Subagent session model

Each subagent gets its own Session, constructed directly rather than by copying the parent
`Session` object. `root_id`/`depth`/`parent` are fields on `Session` itself, not on
`SessionConfig` — `root_id` already exists there today (anticipating exactly this use), `depth` and
`parent` are new.

* same ProcessConfig as the parent
* SessionConfig is copied from the parent (`model_copy()`), then:
  * role is overwritten with the subagent role.
  * tool set is clamped based on the args and klorb-agents role definition
  * same with skills
  * `permission_framework` is *not* an independent copy — see "Shared permission framework state"
    under "Permissions", below.
* the new `Session`'s own fields, set directly (not part of the copied `SessionConfig`):
  * `root_id` = parent's `root_id`.
  * `parent` = a reference to the parent Session.
  * `depth` = `parent.depth + 1` (the session directly interacting with the user has `depth == 0`).
  * `id` gets its own timestamp + coolname-slug value, same as any other session.
  * `session_name` is pre-set to the parent-supplied `session_title` rather than left `None` — see
    "CreateSubagent", below, for why.
* model is whatever the model chosen for the agent role is; either the role's default, or if the
  caller overrode that in the args to CreateSubagent
* system message is initialized based on the role
* the parent agent sent a msg which is the first user message.
* none of the parent's message history is directly embedded in the subagent.

### Lifecycle and teardown

A subagent session that has finished a turn is not destroyed — it sits idle in a "finished" state
so `MessageSubagent` can resume the conversation later. There is no explicit "close this subagent"
tool; instead, a subagent session (and, recursively, any of its own subagents) is torn down when
its parent session ends (session closed by the user, or the process exits). This cascades all the
way down a subagent tree: ending session 1.2 also ends 1.2.1, 1.2.2, and everything beneath them.

### Persistence

Subagent sessions are not persisted to disk as their own `sessions/<subdir>/` — this is the
mechanism `docs/specs/session-persistence.md` already flagged as undefined ("a subagent's state is
expected to be tracked within its root session's own directory, by a mechanism this design doesn't
define yet"): the mechanism is "don't persist it at all." A subagent's entire durable contribution
is the `SystemInterjection` text relayed into its parent's conversation once it produces output —
that relay is already part of the parent's persisted `messages`, so nothing observable is lost by
not separately persisting the subagent's own transcript.

The consequence is that restarting klorb does not resume an in-flight subagent turn. There is no
checkpoint of where a subagent's agentic loop was to resume from, and building one — serializing
mid-tool-call state, re-establishing whatever it was doing — is substantial complexity for benefit
this plan doesn't need: a subagent's job is to answer one bounded question and hand the answer
back, not to carry long-lived state the user would miss on restart the way they'd miss their own
conversation. A subagent that hasn't finished when the harness shuts down simply doesn't survive
the restart, and a new one can be created if the work still needs doing.

To keep the parent's persisted transcript coherent — never left waiting on a `WaitForSubagent` that
can no longer resolve — a graceful shutdown (quit) must cascade-terminate every live subagent
first and relay a terminal note into its parent's conversation, reusing the existing "(Subagent
turn aborted by user)" relay pattern with a distinct message, e.g. "(Subagent terminated: harness
closed before it finished)". This only runs on a graceful shutdown; a crash (killed process, power
loss) loses an in-flight subagent's work with no such note, the same way a crash already loses more
than a clean quit does for any other in-flight session state today.

### Communicating back to the parent

* The parent sent the initial message/prompt to the subagent
* The subagent runs asynchronously with respect to the parent. The CreateSubagent tool returned its
  id promptly (after the subagent is running) and the parent agent is expected to continue to go
  about its business.
* If the subagent finishes first, then the next time the harness has an opportunity to send a msg to the parent,
  the message output of the subagent thru the end of its superturn is provided to the parent as a
  SystemInterjection. (either riding along at the top of a true user message, or attached to some
  tool_response block, or if the parent had actually finished its own turn, we send a degenerate
  "user message" to the parent with the contents of the subagent output in a SystemInterjection. The
  SI should contain a note at the end that the user did not add anything to the prompt, it's just
  the subagent output. The SI should have subject "subagent". It should have metadata fields at the
  top:

  ```plain

  <SystemInterjection subject="subagent">
    id: 1234-abcd-excellent-giraffe
    role: explorer
    title: sub-session title as-provided by the parent goes here, e.g. "find test case for foo bar"

    (...body of response from the subagent comes here...)

    [optional:] (Subagent turn aborted by user)
    [optional:] The user did not provide a prompt this turn; there is only this system interjection
    with the output of a recently-completed subagent.
  </SystemInterjection>
  ```

  * If the parent runs out of work it can do before getting an answer back from the subagent, it should use
    WaitForSubagent.
  * The parent can then use MessageSubagent to send a response and continue its conversation with the subagent.

## Explorer Subagent

The Explorer can use FindFile, Grep, ListDir, ReadFile, ListMemories, SearchMemories, ReadMemory,
EditScratchpad & ReadScratchpad, and WebFetch (and also let's preemptively list WebSearch, for when
it's implemented). It may also use the subagent mgmt tools (CreateSubagent, etc).

Memory access is deliberately read-only: `CreateMemory`/`EditMemory` are excluded even though the
Explorer role otherwise has broad read access, because a memory write persists into future,
unrelated sessions rather than just the current conversation — and Explorer is the subagent role
most likely to be pointed at untrusted content (arbitrary files, web pages) where prompt injection
could try to steer it into planting misleading memories. If the Explorer surfaces something worth
remembering, it reports that fact in its final response; the parent (which has full memory tool
access) decides whether to persist it.

`EditScratchpad`, by contrast, is kept — see "Tool and skill limits" for why the same
prompt-injection concern doesn't carry over to the scratchpad the way it does to memory.

The `enforce_read_only` flag should be set to `True` for the Explorer.

The Explorer can launch more Explorer subagents as well as a Vision subagent.

## VisionAssistant subagent

The job of this subagent is to describe an image to a parent model which either doesn't want the
image in its own context or is blind.

This should use xiaomi/mimo-v2.5; it has a good vision model and it is very inexpensive.

This agent may not actually use any tools, skills, etc.

This needs its own role system prompt.

## Update to Operator prompt

Operator should be encouraged to use subagents if needed, and briefly explain the menu of options.

## Implementation phases

This is too large for one agent's context in one pass. It is split into phases below; each phase
must leave the system fully runnable, with all existing tests passing, before moving to the next.
Subagents are not reachable by any live agent until Phase 2 — Phase 1 is pure plumbing with no
user-visible behavior change. **Every phase that makes `CreateSubagent` reachable by any role must
ship with the full depth/`allow_subagents`/tool-skill-intersection enforcement described in
"Security model and AI safety" active at the same time — that enforcement is not itself a
deferrable phase, since a phase where subagents run without it would be an unsafe intermediate
state.**

### Phase 1: Config, schema, and the intersection engine (no behavior change)

* Add `tools.subagents.maxDepth` to `ProcessConfig` (default 2).
* Add `depth: int` and `parent: Session` to `Session` (`root_id` already exists there). Establish the
  dotted-decimal address scheme (monotonic, never reused) as a display-only value derived from the
  live tree of `parent` links, not a persisted field.
* Introduce the shared-by-reference `permission_framework` state scoped to a session tree (see
  "Shared permission framework state"), and wire `Session.set_permission_framework()` to mutate it.
* Add `klorb/resources/agents.json` with the `klorb-agents` schema envelope, and the
  `AgentDefinition`/`AgentRestrictions` pydantic models. Load it once at process start into an
  immutable registry.
* Implement the tool/skill/subagent_role intersection logic as a standalone, unit-tested function:
  given a parent's live effective sets plus a `restrict_to`, compute the child's effective sets.
  This is the safety-critical core of the whole feature and should be tested in isolation before
  anything calls it.
* Modify `EditScratchpad.is_read_only()` to return `True`.
* Audit existing tools' `is_read_only()`/`category()` overrides for accuracy, and classify
  `CreateSubagent`/`WaitForSubagent`/`MessageSubagent` (added in Phase 2) as read-only ahead of
  time in the design.
* No new tools are registered yet; nothing here is reachable by any agent.

### Phase 2: Explorer subagent, headless-capable, no dedicated panel

* Implement `CreateSubagent`, `WaitForSubagent`, `MessageSubagent`, wired through the Phase 1
  intersection engine, depth check, `allow_subagents` gating, and the `maxConcurrentPerParent`/
  `maxActiveTotal` fan-out caps.
* Implement the Explorer role and its system prompt (`system_prompts.d/roles/explorer/default.md`).
* Wire scratchpad sharing (parent's scratchpad path passed into the child's `Session`), the
  session-naming skip (pre-set `session_name` from `session_title`), and graceful-shutdown cascade
  termination (see "Persistence").
* Implement the `SystemInterjection` relay of subagent output back to the parent, reusing the
  existing standing/one-shot interjection and `ToolResponseEnvelope.system_interjections`
  mechanisms rather than inventing new plumbing.
* Implement the @mention skip-fallback (mentions left as literal `@filename` text, not resolved)
  for messages sent into a subagent, per the "File @mentions" section.
* Simplification for this phase only: route any permission prompt raised by a subagent through the
  existing single-session interactive UI (tagged with the subagent's address/role in the prompt
  text), rather than building the dedicated agents panel yet — today's UI only ever shows one
  session at a time regardless, so no new routing surface is required to make this safe.
* Only the Operator role's `agents.json` entry grants `allow_subagents: true` initially, so this
  capability is opt-in and narrowly reachable.
* Add unit tests for the new tools and an eval case for `CreateSubagent`/`WaitForSubagent`.
* System is runnable end-to-end in headless mode and in the TUI (via the existing single-session
  UI); the VSCode plugin is unaffected since it doesn't yet expose the new tools' output specially.

### Phase 3: TUI subagents panel — done; read before starting Phase 4

Implemented in `klorb/src/klorb/tui/widgets/subagents_panel.py` and `klorb/src/klorb/tui/mixins/
subagents_panel.py` (plus small hooks into `interactions.py`, `key_actions.py`,
`prompt_submission.py`, `status_bar.py`, `task_sidebar.py`); documented in full in
docs/specs/subagents.md's "Subagents panel (TUI)" section. Phase 4 is a straight visual/framework
port of this same design to the webview — the bullets below are corrections that came out of
actual UI review against a running TUI, folded in here so Phase 4 doesn't have to rediscover them
by going through the same round of feedback again:

* **No depth-based row indentation.** A subagent's dotted-decimal address (`"1.1.1"`) already
  reads as nested by virtue of being longer than its parent's (`"1.1"`) — don't additionally
  indent deeper rows, it just wastes horizontal space for no added clarity.
* **One leading marker character, not several stacked ones.** A row gets exactly one marker
  before its address: `!` if it has an ask waiting on the user selecting it (wins over the next
  case), else `*` while its turn is actively running, else a blank. Do not give "needs attention"
  and "running" each their own separate marker slot — one is enough, and attention always takes
  visual priority since it's the more actionable state.
  See `SubagentsPanel._render_row_label` for the exact precedence.
* **Keep the panel's own chrome minimal — no extra inner border/background box** around the row
  list distinguishing it from the rest of the panel. The single accent-colored divider between
  the panel and the main conversation view is the only border; the row list's own default
  component styling (Textual's `OptionList` ships a border and background of its own, which had
  to be explicitly cleared — see the `DEFAULT_CSS` comment in `subagents_panel.py`) should not
  reintroduce a second nested border/background. The *selected-row* highlight and *hover*
  background are good and worth keeping — the fix here is scoped to the surrounding chrome, not
  row selection styling.
* **Selection is global, including the root session**, not "the subagent tree plus a separate
  root mode": exactly one thing is selected at a time (root or any node in the tree), and every
  interactive ask (permission/question/escalation — not just a subagent's) only surfaces once its
  *owning* session is the one currently selected. This turned a Phase-2 simplification ("asks
  always interrupt whatever's on screen") into the actual, correct behavior — carry the same
  selection-gates-every-ask model into the webview rather than re-introducing the simplification.
* **The transcript view needs its own pin-to-bottom tracking**, independent of (and by the same
  rule as) the main conversation view: follow new content to the bottom only if the reader hadn't
  already scrolled away from it. A naive "always scroll to bottom on update" reads as broken the
  moment someone scrolls up mid-turn to reread something.
* **Any token/usage tally in the UI should reflect the *selected* session**, not always the root
  — recompute it on selection change and on every transcript update, from whichever session is on
  screen.
* **The trailing status line under a subagent's transcript has four states**, not two: "still
  working" (running) / "task complete" (finished normally) / "interrupted" (finished because of a
  cancel) / "sending interrupt…" (cancel requested but not yet landed — shown immediately on the
  keypress, not after the background turn actually unwinds, since that can take a moment). Distinct
  "task complete" vs. "interrupted" wording matters — don't collapse them into one generic
  "finished" state.
* **A Stop/interrupt action must give immediate feedback**, before the thing being stopped has
  actually stopped: show something like "sending interrupt…" the instant the user acts, not only
  once the cancellation has taken effect — the underlying operation can take a visible moment to
  actually wind down, and silence during that gap reads as the keypress having done nothing.
* **Every other piece of chrome that names "the current session" needs to follow the selection
  too**, not just the transcript/token-tally/prompt-input pieces called out above — the window
  title (model name, and its effort/reasoning-mode indicator if applicable) and any "current
  session title" label both need to read from whichever session is selected, since a subagent can
  run an entirely different model than the root. Forcing a re-render when the selected session
  changes may need an explicit nudge if the underlying framework only re-renders on a bound
  value's own change (the TUI's `Header` only redraws when Textual's `sub_title` reactive value
  itself changes, so switching *which* session's model to show — without the value happening to
  differ — needed an explicit redraw call; the debugging equivalent of "the label just doesn't
  update" is easy to miss without deliberately testing selection changes against two sessions
  running two different models).
* **A subagent's transcript must render everything a live turn would, not just tool calls** — in
  particular, a message reclassified to `tool_use` because it carries tool calls can *also* carry
  its own plain-text commentary (including, often, the subagent's actual final answer, if that
  arrived in the same round as its last tool calls); rendering only `tool_calls` and ignoring that
  message's own text silently drops it. Similarly, a "thinking" message's plain-text content and
  its structured reasoning-details payload can come from two independent, not-necessarily-in-sync
  provider streams — a naive render can show an empty thinking block even though real reasoning
  text exists, recoverable from the structured payload. Both gaps are general properties of
  rendering a finished `Session.messages` list from scratch (not specific to the TUI or to
  subagents) — see `klorb.tui.formatting.resolve_thinking_body_text` and the `tool_use`-content
  handling in `klorb.tui.mixins.subagents_panel._mount_subagent_messages`/`klorb.tui.mixins.
  rendering._mount_restored_history` for the reference fix; port the same two checks into
  whatever renders a subagent's transcript from its message list in the webview.

### Phase 4: VSCode plugin panel parity

* Mirror Phase 3's panel in the webview, docked above the Tasks panel, following the existing
  `vscode-plugin/src/webview/features/tasks/` structure as the reference pattern for a new
  `features/subagents/` feature (barrel `index.ts`, host↔webview message protocol additions in
  `src/shared/`).
  * Like the VSCode port of "Tasks", this should be a panel adapted to be docked at the top of
    the plugin, as opposed to its own column on the right side of the screen as in the TUI.
* Same selection behavior, `HistoryView` switching, attention indicator, context / token estimate
  monitor switching, and Stop-button semantics as the TUI — including every correction noted under Phase 3
  above, which apply here just as much as they did there.
  * The address/title rows / role footer, etc. should also all update. In the VSCode plugin,
    the "current model" and thinking level are rendered as clickable buttons; but you cannot
    change the model for a subagent once it's underway, so these should not be active as buttons
    when a subagent is selected; the model name and thinking effort behavior should just be
    informational in a subagent-selected context.
* Check **transcript completeness** while you're at it. We discovered a preexisting bug in the TUI
  where a `tool_use`-role message's own `content` (commentary alongside its tool calls — often the
  actual final answer) was being silently dropped; both the subagent panel and root's
  restored-history rendering now mount it as a response block. Make sure that these items are being
  properly rendered in the VSCode plugin as well.
  * This was fixed by adding `resolve_thinking_body_text` in the TUI code: when a "thinking"
    message's plain-text `content` is empty but `reasoning_details` carries real `text`/`summary`
    fields (two independent, not-always-synced provider streams), reconstructs the `<Thinking>` body
    from those instead of showing an empty block.

### Phase 5: Operator prompt polish

* Update the Operator role's system prompt to mention subagents as an available capability
  and mention a `/launch-explorer-subagent` skill.
* Create an internal `/launch-explorer-subagent` SKILL.md file in klorb/resources/.
  It should explain when and why to launch a subagent with the `explorer` role.
  Modify agents.json so this skill is explicitly passed on to the `explorer`, so when it wants
  to also launch subagents, it sees this skill.
* The `assigned_task_id` future work described earlier (auto-starting a subagent on a specific
  todo item) is explicitly out of scope for all of the phases above, since it first requires
  reworking the todo tools' current greedy auto-assignment behavior; it should be tracked as its
  own follow-up rather than folded into this phased rollout.

### Phase 6: VisionAssistant subagent

* Add the VisionAssistant role (no tools/skills, `xiaomi/mimo-v2.5` default model) and its system
  prompt.
* There is an open design question that must be solved before implementing phase 6: if the parent
  agent is not a vision-capable model, how do images get to the parent in such a way that it can
  then convey them to the subagent? Maybe the presence of this subagent capability means that we
  should enable image drag/drop in the VSCode plugin; but then we will need to take care not to
  actually inject a MessageFragment that would look like a top-level image attachment, as that
  would cause the model to just fail to read it; we need to put the base64 data in the actual
  text prompt received by the parent agent, and give it instructions that it should invoke a
  subagent, as well as directions on how to embed it for the child. We probably need a special
  param for CreateSubagent that causes us to format a multimedia multi-fragment Message in this
  case...
* Extend `agents.json` and the Operator's and Explorer's `subagent_roles` restriction to permit
  launching it.
* Add a `/launch-vision-subagent` internal skill like `/launch-explorer-subagent` and plumb it
  into the Explorer and Operator in the same way.
* No panel or tool-plumbing changes needed — this phase is purely a new role definition exercised
  through the infrastructure already built in Phases 1-5.
