
# Hooks and Events

Definitions:

* **Hooks** are moments in the application lifecycle where the user can inject their own logic.
  (Either a bash script, or adding to the agent conversation, or invoking a different agent/classifier)
* **Events** are moments that occur as part of the planned lifecycle or triggered as-they-come by various
  influences (other systems that can do I/O that interacts with our system, or as a direct reuslt of
  agent behavior) that can trigger processing and update the agent conversation.

In other words: hooks alter the planned lifecycle. Events inject context into the conversation.

## Hooks

* Hooks are defined in the config json in a `hooks: { HookListConfig }` block

e.g:

```json
{
  "hooks": {
    "onProcessStart": [
      {
        "type": "bash",
        "shell": "a literal shell string to run like with bash -c",
        "command": ["separate", "argv", "elements", "instead", "of", "shell", "pick", "one"],
      },
      { /* HookConfig */ },
      /* more HookConfig instances */
    ],
    "onSessionStart": [
      /* list of HookConfig */
    ],
    "onSubmitUserPrompt": [
      {
        "type": "classifier",
        "prompt": "add summary bullet points at the end of the user prompt digesting what the user wrote.",
        "name": "A name for this particular HookConfig instance to tell it apart from others in the list"
      }
    ],
    "onAgentTurnEnd": [
      {
        "type": "chat",
        "prompt": "The band plays on! Keep going...",
        "filter": {
          /* This is a HookConfigFilter, explains when this hook config is eligible. */
          "not": { "matches": "definitely done"  /* This is a nested HookConfigFilter. */ }
        }
      }
    ],
    "onSessionEnd": [
      /* list of HookConfig */
    ],
    /* other hook-name-to-hook-config elements. */
  },
  /* rest of process config. */
}
```

Multiple config files in the stack can declare hook handlers. For each hook (onProcessStart, onSessionStart, etc),
we concatenate the lists of hook handlers from all the trusted configs in the stack.

**security:** An untrusted workspace config cannot add any hooks or events. This falls out of the existing config
stack for free: `load_process_config()` already skips a workspace's `.klorb/klorb-config.json` layer *entirely*
(never opened, not just filtered) whenever `workspace.trusted` is `False` (see docs/specs/projects-and-trust.md).
`hooks`/`events` need no dedicated enforcement — they're ordinary keys read through that same layering, so an
untrusted workspace's config simply never contributes to either list.

### Configuration placement and merge behavior

`hooks` and `events` are flat, process-scoped keys (`PROCESS_KEY_MAP`, top-level in `klorb-config.json`, not
inside `sessionDefaults`) — not per-session settings. A hook/event policy is a cross-cutting rule from the
config stack, not something a session mutates interactively at runtime the way a `commandRules` grant is, so it
belongs alongside `thinking.tokenBudgets` and friends rather than `readDirs`/`commandRules`. This also sidesteps
needing per-session hook config: `ProcessConfig` fields are already required to be identical across every
concurrently running session (see docs/specs/process-and-session-config.md), which is exactly the semantics
hooks/events want.

Each of `hooks`/`events` is a single flat key whose value is an object keyed by hook/event name
(`onProcessStart`, `FileSystemModified`, ...), each holding a list of handler configs. The merge behavior across
config-stack layers is a fifth variant alongside the four docs/specs/process-and-session-config.md already
enumerates: **named-list concatenate** — for each hook/event name present in a layer's `hooks`/`events` object,
that layer's list is appended to whatever list earlier layers already built for that same name (the same idea
`readDirs`'s `deny`/`ask`/`allow` concatenation uses, generalized from three fixed subkeys to an open-ended,
finite set of hook/event names). Add this variant to `process-and-session-config.md`'s list when this lands.

* The *hooks* are the moments in the lifecycle (e.g. `onProcessStart`).
* The *hook handler* is the action that is taken in that moment: running a particular script, or calling a classifier with a specific prompt.
* A *hook handler type* is the flavor of hook handler; how do we actually launch a hook handler? There are three today:
  * `bash` - run a bash command. json goes in on stdin and comes out on stdout.
  * `classifier` - appeal to a separate, fast classifier. A given prompt plus some json goes in and json comes out.
  * `chat` - inject a message into the main agent conversation session.

### Hook Handlers

#### type=bash

* Use `shell` to give a single big argv string like would be passed to `bash -c '...'`
* Use `command` to give an array of individual argv elements.

We will do substitution of `${home}` and `${workspaceRoot}` into `command`. But not `shell`.

The handler gets json in on its stdin and emits json on its stdout. The return code is also
a signal attended to by the hook infrastructure; non-zero is a failure, zero is a success (unless
the json has a well-known success field that is marked false).

A `bash` handler's subprocess is sandboxed the same way `BashTool` sandboxes an agent-issued command: built
via `klorb.sandbox.build_bwrap_argv()`, the same function `BashTool._bwrap_prefix` calls
(`klorb/src/klorb/tools/bash.py`) — not a second sandboxing path. It also gets the same `share_env`/`set_env`
passthrough `BashTool` gives an agent command, plus one new addition: `KLORB_HOOK_ENV_FILE` pointing at a
session-scoped env file under the session state dir, so a hook script can read/customize values without them
being visible to the model as tool call args. The hook is explicitly given both readFiles and writeFiles
access to the KLORB_HOOK_ENV_FILE. This resolves the "session-specific env file... point to it with
an env var when running hooks" item already on `TODO.md`'s feature backlog — remove that item once implemented.
A hook subprocess is bounded by a timeout (config key `hooks.bash.timeoutSeconds`, defaulting to the same
value `tools.bash.timeout` uses); a timeout or non-zero exit is treated as that handler contributing nothing to
the aggregate outcome (not a crash) and is logged at `warning`, per "Error handling" below.

#### type=classifier

We run a fast classifier the same shape as the one used for session naming (`klorb.session_naming.
generate_session_name`, `klorb/src/klorb/session_naming.py`) — same pattern, not the same call: a hook
classifier gets its own system prompt (explaining it's helping run a hook, and that the incoming user-supplied
text is untrusted content, not instructions — mirroring `session_naming.py`'s own jailbreak-resistant framing),
its own strict-JSON response schema shaped like `HookOutput` (structured output the same way
`generate_session_name`'s `_response_format()` is), and the same hard-timeout/never-raises contract
(`classifier.timeout`/`classifier.e2eTimeout` config keys, reused rather than duplicated). We feed it the prompt
that the user supplied, as well as information about the hook-generating event: the json goes first, then the
user prompt. A classifier failure or timeout degrades the same way a bash handler's does — contributes nothing,
logged at `warning`, the turn/lifecycle event proceeds.

#### type=chat

This causes a new "user message" to be sent back to the agent to continue the conversation autonomously.
Especially if used at the end of the agent turn, this should be used with a filter so that it does not
run in an infinite loop.

`Session.send_turn()` (`klorb/src/klorb/session/mixins/turns.py`) is already the real library-level "start a
turn" primitive — both the TUI (`PromptSubmissionMixin._submit_prompt`) and the ACP server
(`TurnBridge.run_turn()`, `klorb/src/klorb/server/turn_bridge.py`) already call it directly, so a `chat` handler
kicking off a turn is not, by itself, a gap.

The actual gap is narrower: *deciding* whether to call `send_turn()` fresh or queue instead, based on whether a
turn is already running. The TUI makes that decision itself, in-process
(`PromptSubmissionMixin.on_prompt_input_submitted` checks `_turn_in_flight`, then calls either `_submit_prompt`
or `_queue_prompt`/`enqueue_queued_message`). The ACP server never has to make it in library code at all — the
protocol enforces it instead: a second `session/prompt` while one is in flight is rejected outright at the
JSON-RPC level, and a client that knows a turn is running is expected to call `_klorb/enqueueMessage` itself
rather than retry `session/prompt`. Both callers, in other words, have *something outside `Session`* — a user at
a textarea, a client watching for a rejected request — that already knows whether a turn is in flight.

A hook/event dispatcher has neither: nothing prompts it, so nothing external already knows the session's state.
It needs the check-and-branch itself, as a small `Session`-level helper (e.g.
`Session.start_turn_or_enqueue(text)`) that inspects turn-in-flight state and calls either `send_turn()` or
`Session.enqueue_queued_message()` — this is the one new piece of library plumbing this feature actually needs,
not a new way to start a turn.

As a hard backstop beyond "use a filter" (a filter is config, and config can be wrong), a `chat` handler that
triggers a new turn increments a per-session chained-turn counter (config key
`tools.hooks.maxChainedTurns`, a modest default like `5`); once the cap is hit, further auto-chained turns from
hooks are refused (logged at `warning`) until a real user- or tool-driven turn resets the counter — the same
fail-safe shape as `max_tool_calls_per_turn`/`max_tool_calls_per_session` (docs/specs/process-and-session-config.md).

### Filters

Filter clauses can go inside a hook config, which determine if that particular hook config is valid to run.

Each filter should contain exactly one of the fields defined in this example payload:

```json
{
  "matches": "literal string",
  "pattern": "^some regex$",
  "contains": "literal string, as a substring; i.e. as if it ran ^.*contains.*$",
  "any": [ /* list of filters, any one of which must eval to true for this to eval to true */ ],
  "all": [ /* list of filters, all of which must eval to true for this to eval to true */ ],
  "not": { /* a filter */ }
}
```

### Hook handler execution

* Bash commands are run in an isolated one-off session, cwd=workspaceRoot. Same bubblewrap as the agent cmds get.
* Multiple valid handlers for one event run **sequentially, as a chain**: each handler's `HookOutput` (rewritten
  `tool_args`, `message`, etc.) becomes part of the next handler's input, so a later handler can layer on top of
  an earlier one's changes rather than clobbering or racing it. The chain runs in *some* fixed linear order (the
  concatenated-list order the config stack produced — see "Configuration placement and merge behavior" above),
  but that order is deliberately not part of the documented contract: a user should not write handlers whose
  correctness depends on knowing which config layer's handler runs before another's. This keeps room to change
  the concrete ordering later without it being a breaking change.
* If the hook is determining whether something succeeds or not, we use the strictest outcome across the whole
  chain, computed with the same allow/ask/deny reduction `klorb.permissions.table.stricter_verdict` already
  implements for permission evaluation elsewhere (`klorb/src/klorb/permissions/table.py`) — reused, not
  reimplemented:
  * any "reject" is a rejection no matter if others accept.
  * allow/ask/deny fail closed to "any deny" first, then "any ask"; allow only if all allow.
  * if hooks are silent about a judgment that means the default judgment takes hold. i.e., if we would ordinarily
    ask the user for permission, just ask for permission. if we would ordinarily auto-deny (or
    auto-accept) b/c no user is present, then we auto-deny / auto-accept depending on the system
    mode.

### Error handling

* A handler that times out, exits non-zero (bash), or fails to produce valid `HookOutput` json is treated as
  contributing nothing to the chain (the next handler, if any, sees the previous *valid* output, not a
  synthesized failure `HookOutput`) — logged at `warning`, never raised out to crash the lifecycle moment the
  hook was attached to. A hook is a policy overlay, not something that should be able to take down a session by
  misbehaving.
* A malformed hook/event config entry (unknown `type`, missing required field) is caught at config-load time,
  collected into `ProcessConfig.config_warnings` — the same place an unrecognized on-disk key already goes
  (docs/specs/process-and-session-config.md) — rather than failing silently or crashing process startup.

### Available hooks

* onProcessStart -- when bin/klorb starts up in this workspace (matches refer to `event`)
* onSessionStart -- when a session is started, resumed, or cleared (matches refer to `event`). Fires once
  workspace trust is settled for this session's startup, not necessarily at raw `Session` construction — for a
  headless one-shot run or an ACP `session/new` those are the same instant, since neither entry point ever
  bootstraps interactively; for the TUI they're not, so this hook fires from `_resolve_workspace_trust()`
  (`klorb/src/klorb/tui/mixins/workspace_bootstrap.py`), the same barrier
  `_run_startup_workspace_and_initial_message()` already awaits before submitting the session's own first turn.
  One hook, one firing, always trust-settled by the time it fires. `HookInput` carries `workspaceTrusted: bool`
  and `workspaceJustBootstrapped: bool` (true only when this session's start is what triggered a first-time
  trust decision) alongside `workspaceRoot`. A *later*, mid-session trust change (`>Trust workspace`,
  `_klorb/trustWorkspace`) is not this hook firing again — see the `WorkspaceTrustChanged` **event** under
  "Events" below for why that's a different kind of thing entirely.
* onSubmitUserPrompt -- when a user prompt is about to be sent to the agent (matches are on 'message')
* onRequestPermission -- when the agent needs to ask permission for file/dir access or tool/skill use, etc.
* onToolUse -- when the agent is about to run a tool. preprocesses the tool inputs (matches are on tool_name)
* onToolResult -- preprocesses the tool use result before returning it to agent. (matches are on message)
* onSubagentStart -- like onSubmitUserPrompt but when we kick off the subagent.
* onSubagentTurnEnd -- like onAgentTurnEnd.
* onAgentTurnEnd -- after the agent has printed its final msg and declared its turn over.
* onSessionEnd -- when we are suspending or destroying a session (matches: event)
* onProcessEnd -- when bin/klorb is exiting. (matches: event)

#### Scope across the subagent tree

Subagents (docs/specs/subagents.md) are separate `Session` objects nested under a root session. Hooks split by
scope:

* **Root session only:** onSessionStart, onSessionEnd, onSubmitUserPrompt, onAgentTurnEnd. These never fire for
  a subagent's own turns.
* **Whole tree, every session:** onToolUse, onToolResult. A subagent using `Bash` (or any other tool) fires these
  exactly like the root session would, tagged with `role`/the firing session's id in `HookInput` so a `filter`
  can single out subagent activity if it needs to.
* **Subagent lifecycle, analogous to the root-only pair above:** onSubagentStart mirrors onSubmitUserPrompt
  (fires when a subagent's turn is kicked off, whether via `CreateSubagent`, `MessageSubagent`, or
  `_klorb/subagentPrompt`), onSubagentTurnEnd mirrors onAgentTurnEnd (fires when a subagent's turn ends).
* **Neither has a session yet:** onProcessStart/onProcessEnd fire before any root session exists / after it
  (and its whole subagent tree) has already closed.

The following `HookInput` json is provided as input to bash or the classifier for these:

```json
{
  "hook": "onProcessStart", // hook name
  "name": "the name the user gave the relevant hookconfig in the config, if any" | null,
  "args": {
    // either 'shell' or 'command', or 'prompt'
  },
  "workspaceRoot": "/path/to/wsroot",

  "event": "NewSession|ResumeSession|SuspendSession|DestroySession", // for session start/end
  // or Startup|Shutdown for process start/end

  "message": "the user prompt (if onSubmitUserPrompt) or agent response (if onAgentTurnEnd).",

  "tool_name": "a tool being used if onToolUse/onToolResult",
  "tool_args": { /* args to the tool */ },

  "role": "if it's about the main agent (typically 'operator') or a subagent",

  // onRequestPermission's own fields are deferred; see "Future work" below.
}
```

the output (whether from bash hook or a classifier or an event handlers) is:

`HookOutput:`

```json
{
  "success": True/False, // whether we allow the turn / action to continue. the 'onSession..' are not
                         // cancelable. We are definitely starting (ending)
                         // Process exit non-zero is also equivalent to declaring success=False
  "tool_args": { /* rewritten tool args, if relevant. */ },
  "permission": "allow|ask|deny", // if the agent was asking permission
  "message": "a message to send to the agent. this may rewrite a user prompt (onSubmitUserPrompt) "
      "or continue chat w/ a new user turn, if onAgentTurnEnd. it also may be feedback given with a "
      "permission denial.",
  "interrupt": false // if `message` is set and the agent is mid-turn on a long think, whether to break in
      // now (like a user queuing a message then hitting ^C) rather than waiting for the next natural
      // delivery point. Only meaningful alongside `message`; see "Event output" below.
}
```

## Events

Events happen at arbitrary times, not necessarily at specific lifecycle moments. This is a responsive system.

### Config schema

The EventListConfig object goes in the process config like so:

```json
{
  "events": {
    "FileSystemModified": [
      {
        /* This is one 'EventConfig' Or maybe a FSModified subclass of it b/c 'watch' is event-specific. */
        "watch": "some/path/within/workspace" // if a dir, watch it with **. if a file, watch the file.
        "action": { /* Same schema as HookConfig: bash or chat, etc... */ }
      },
      { /* More EventConfig objects */ }
    ],
    "Timer": [
      {
        // use one of cron or interval in a TimerEventConfig:
        "interval_minutes": 10, // every N minutes
        "cron": "30 2,4,6,8 * * *", // at various early hours of the day.
        "action": /* ... */ }
    ],
    "WorkspaceTrustChanged": [
      {
        /* No extra selector field, unlike FileSystemModified's 'watch' or Timer's schedule — there's nothing
           to select, just the action to run whenever the moment occurs. */
        "action": { /* Same schema as HookConfig: bash or chat, etc... */ }
      }
    ],
    // subscribe to other events...
  },
  /* the rest of the process config. */
}
```

Terminology:

* "FileSystemModified" is an *event*.
* A json obj in the array associated with it is an *event handler*, one handler
  among many subscribed to the same event.

Available events:

* FileSystemModified
* Timer
* WorkspaceTrustChanged

### FileSystemModified event

* Runs the specified action when the filesystem is updated. We put an inotify watcher on this, attached
  to the root session — a "chat" action's message always targets the root session's conversation, never a
  subagent's, regardless of which session happens to be running at the moment the event fires.
* Built on `watchdog.observers.Observer`/`FileSystemEventHandler` (already a pinned runtime dependency,
  `klorb/pyproject.toml`), the same library `klorb.tui.workspace_file_index` already uses for the `@`-mention
  file index, including its debounce pattern (`_DEBOUNCE_SECONDS`/`threading.Timer`-based flush) — generalized
  here to a configurable debounce window instead of that module's fixed one.

### Timer event

* Runs the specified action either on an interval (measured in minutes; accepts a float, but shall be no
  more frequent than once every 10 seconds) or on a cron timer.
* **Best-effort only, not real cron.** Nothing in klorb today stays running independent of a live TUI session or
  a connected ACP client (`klorb server` exits the moment its one client disconnects — see docs/specs/klorb-server.md)
  — there is no persistent daemon mode. A `Timer` handler only fires while *some* klorb process for this
  workspace already happens to be running for other reasons; a fire time that elapses while nothing is running
  is simply missed, not queued or caught up on restart. This must be stated plainly in `hooks.md` (see
  "Documentation" below) so a user doesn't mistake this for a real scheduler. A genuine persistent daemon mode is
  future work (see "Future work" below) — building one is out of scope here.

### WorkspaceTrustChanged event

* Fires when a workspace's trust decision is made or changed against an already-live root session: the TUI's
  `>Trust workspace` command (`event: "TrustCommand"`) or `_klorb/trustWorkspace` over ACP
  (`event: "AcpTrustWorkspace"`) — see docs/specs/projects-and-trust.md. This is deliberately an *event*, not a
  hook: unlike `onToolUse`/`onAgentTurnEnd`/etc., it isn't tied to a specific step in klorb's own planned turn
  lifecycle — a user or client can trigger it whenever they like, mid-turn or between turns, the same
  "triggered as-they-come" character `FileSystemModified`/`Timer` have (see "Hooks"/"Events" definitions at the
  top of this document).
* Always targets the root session, same as `FileSystemModified` — never a subagent's conversation, regardless of
  which session happens to be active when it fires.
* This is distinct from `onSessionStart`'s own `workspaceTrusted`/`workspaceJustBootstrapped` fields (see
  "Available hooks" above), which report a session's *initial* trust state as part of its planned startup
  sequence — a one-time, planned fact, not a later, unplanned change.
* Resolves the `TODO.md` "onWorkspaceTrust hook, executed within the Session" backlog item (as an event, not a
  hook, per the reasoning above) — remove that item once implemented.

### Event input

* looks basically like `HookInput`
  * fs events should also include a bunch of `"fs_updates": [ UpdateObj ]`
    * UpdateObj has a field for `event` (created, deleted, modified) and another field for `path`.
    * fs events are debounced by 10 seconds. All inotify events that occur in a burst are delivered
      as one event.

### Event output

Same schema as `HookOutput` (see "Hooks" above, including the `interrupt` field).

* If the event creates a `message` then it will be passed to the agent, via the same
  `Session.start_turn_or_enqueue` helper a `chat` hook handler uses (see "type=chat" above).
* If the agent is running, it's a queued user interjection.
* If the agent is not running, we start a new user turn; add a prefix that says:
  "An event has resumed this conversation:\n(event handler msg here)"
* If the agent keeps running on its own on a long think, this might take a while. If `interrupt` is true, it's
  like when a user queues a message for the agent and then hits ^C to disrupt what the agent is currently doing,
  so the queued message breaks into the conversation — this needs new wiring on top of the turn's existing
  `cancel_event` (`klorb/src/klorb/session/events.py`'s `TurnEventHandlers`), since today nothing but a literal
  user keypress sets it.
* The same chained-turn safety cap described under "type=chat" above applies here too — an event that fires
  repeatedly (e.g. a noisy `FileSystemModified` watch) can't auto-chain turns without bound.

## Documentation

Start a `docs/user/` dir at the repo root (alongside `docs/specs/`, `docs/adrs/`, `docs/plans/` — a new sibling,
not nested under any of them). Move `klorb/usage.md` to `docs/user/usage.md`, and update every reference to its
old path: `README.md`, `klorb/README.md`, `.claude/skills/add-cli-flag/SKILL.md`, and
`docs/specs/model-framework.md` all currently link to `klorb/usage.md`. Add `docs/user/hooks.md` that details
this feature in a way a technical user can follow — hook/event list, handler types, filters, config examples,
the `Timer` best-effort caveat, and the automatic workspace-trust gating — without the how-it's-implemented
detail that belongs in `docs/specs/hooks-and-events.md` instead.

## Implementation phases

This is too large for one agent's context in one pass. Each phase below leaves the system fully runnable, with
all existing tests passing, before moving to the next. Hooks/events are inert by default (empty config), so no
phase before the last changes any existing behavior for a user with no `hooks`/`events` config.

### Phase 1: Config schema and the pure filter/merge logic (no behavior change)

* Add a new `klorb.hooks` package (`klorb/src/klorb/hooks/`), alongside the existing `klorb.permissions`/
  `klorb.session`/`klorb.workspace` top-level packages.
* Add pydantic models: `HookConfig` (`type`, `shell`/`command`/`prompt`, `name`, `filter`), `HookConfigFilter`
  (`matches`/`pattern`/`contains`/`any`/`all`/`not`), `EventConfig`/`FileSystemModifiedEventConfig`/
  `TimerEventConfig`/`WorkspaceTrustChangedEventConfig`, `HookInput`, `HookOutput`, `EventInput` — matching the
  shapes documented above.
* Add `hooks`/`events` entries to `PROCESS_KEY_MAP` (`klorb/src/klorb/process_config.py`) and implement the
  named-list-concatenate merge variant described in "Configuration placement and merge behavior" above, in
  whatever function currently implements the four existing variants. Add both keys, empty by default, to
  `klorb/src/klorb/resources/default-config.json`.
* Implement `evaluate_filter(filter: HookConfigFilter, subject: str) -> bool` as a standalone pure function —
  `matches`/`pattern`/`any`/`all`/`not`, recursively. Unit-test this in isolation; it's the piece every later
  phase's dispatch logic depends on.
* Implement the chain-ordering/merge helper that turns "N config layers, each with a list of handlers for hook
  X" into the one deterministic linear order described under "Hook handler execution" above.
* No hook handler is dispatched anywhere yet, and no existing lifecycle code changes.

### Phase 2: Dispatcher, `bash` handler type, and the four simplest hook points

* Implement `klorb.hooks.dispatcher.HookDispatcher` (name illustrative): given a `ProcessConfig`, a hook name,
  and enough context to build a `HookInput`, resolves the ordered handler chain (Phase 1's helper), runs each
  handler, and folds results per "Hook handler execution" and "Error handling" above.
* Implement the `bash` handler type: subprocess spawn via `klorb.sandbox.build_bwrap_argv()` (same function
  `BashTool._bwrap_prefix` uses, `klorb/src/klorb/tools/bash.py`), `HookInput` json on stdin, `HookOutput` json
  parsed from stdout, timeout enforcement, `share_env`/`set_env` passthrough plus the new
  `KLORB_HOOK_ENV_FILE` variable (this phase can point it at a placeholder/empty file — wiring a real
  session-scoped env file's *contents* is a separate concern, not blocking here).
* Wire `onProcessStart`/`onProcessEnd` into `klorb.cli.main()`, and `onSessionStart`/`onSessionEnd` into
  `Session` construction/close (root session only) — for the TUI, `onSessionStart` fires from
  `_resolve_workspace_trust()` (`klorb/src/klorb/tui/mixins/workspace_bootstrap.py`), the same barrier
  `_run_startup_workspace_and_initial_message()` already awaits before the session's first turn, so trust is
  always settled (`workspaceTrusted`/`workspaceJustBootstrapped` in `HookInput`) by the time it fires; for
  headless/ACP it fires right at construction, since trust is already final there. See "Available hooks" above.
* Add `logger.debug()` at dispatch and at each subprocess spawn, per this repo's logging conventions.
* Unit tests: a scripted handler (a temp script echoing canned `HookOutput` json) exercising dispatch, chaining,
  and the four wired hook points end to end. `classifier`/`chat` handler types are recognized by the config
  schema (Phase 1) but not yet dispatchable — a config naming one is accepted, just never invoked.

### Phase 3: Remaining lifecycle hooks, `classifier`/`chat` handler types, chained-turn safety cap

* Wire `onSubmitUserPrompt`, `onAgentTurnEnd` (root session only) into wherever a turn starts/ends
  (`klorb/src/klorb/session/mixins/turns.py`'s `_dispatch_turn`), `onSubagentStart`/`onSubagentTurnEnd` into the
  equivalent subagent lifecycle points (`klorb.agents`), and `onToolUse`/`onToolResult` into
  `SessionToolExecutionMixin._run_tool_calls` (`klorb/src/klorb/session/mixins/tool_execution.py`) — firing for
  every session in the tree, tagged with the firing session's `role`/id, per "Scope across the subagent tree"
  above.
* Implement the `classifier` handler type: its own system prompt and strict-JSON `HookOutput`-shaped schema,
  reusing `classifier.model`/`classifier.timeout`/`classifier.e2eTimeout` config plumbing and the
  timeout/never-raises pattern `klorb.session_naming.generate_session_name` already establishes — a sibling
  implementation, not a call into that function.
* Implement the `chat` handler type: add the `Session.start_turn_or_enqueue(text) -> None`-shaped library method
  described under "type=chat" above (checks turn-in-flight state, then calls `send_turn()` or
  `enqueue_queued_message()`), used both here and by Phase 4/5's event delivery. Add the
  `tools.hooks.maxChainedTurns` cap (new `SessionConfig`/config-key entry, same shape as `max_tool_calls_per_turn`)
  and enforce it.
* Build the allow/ask/deny chain-reduction helper for `onToolUse`'s ability to veto a tool call, reusing
  `klorb.permissions.table.stricter_verdict` rather than reimplementing the reduction.
* Unit + integration tests: a full turn with tool calls exercising `onToolUse`/`onToolResult`; an
  `onAgentTurnEnd` `chat` handler chaining a follow-up turn and the cap tripping after `maxChainedTurns`.

### Phase 4: FileSystemModified and WorkspaceTrustChanged events

* Implement the watcher in `klorb.hooks` (e.g. `fs_events.py`) using `watchdog.observers.Observer`/
  `FileSystemEventHandler`, following `klorb.tui.workspace_file_index`'s existing debounce pattern
  (`klorb/src/klorb/tui/workspace_file_index.py`) generalized to a configurable window (default 10s, per
  "Timer event"'s floor — reuse the same floor constant if one already exists there, else add one shared
  between the two events).
* Start the watcher at root-session start (workspace resolved), tear down at root-session end; deliver
  `fs_updates` via `EventInput`, route output through Phase 3's `Session.start_turn_or_enqueue` helper, always
  targeting the root session.
* Wire `WorkspaceTrustChanged`'s two firing points, both mid-session against an already-live root session: the
  TUI's `>Trust workspace` command (`ReplApp.trust_workspace()`) and ACP's `_klorb/trustWorkspace`
  (`KlorbAcpAgent._apply_workspace_config`) — see "WorkspaceTrustChanged event" above. Needs no watcher/scheduler
  infrastructure of its own, just the same dispatch/delivery plumbing this phase already builds for
  `FileSystemModified`.
* Unit tests against a real temp-directory watch (or a fake `Observer` if that proves flaky in CI) covering
  debounce batching and the create/modify/delete `UpdateObj` shape; a test for the `>Trust workspace`/
  `_klorb/trustWorkspace` firing of `WorkspaceTrustChanged`.

### Phase 5: Timer event (best-effort)

* Implement interval/cron scheduling in `klorb.hooks` (e.g. `timer_events.py`) using an in-process timer loop
  that only runs for the lifetime of the owning process — no new persistent daemon (per the "best-effort only"
  decision under "Timer event" above).
* Add a cron-string-parsing dependency (via the `add-python-dependency` skill — a well-established, permissively
  licensed library; evaluate options at implementation time rather than picking one here) or a narrowly-scoped
  hand-rolled parser if the full cron grammar is overkill for the fields this plan actually supports.
* Enforce the "no more frequent than once every 10 seconds" floor at config-validation time (a `TimerEventConfig`
  requesting tighter is a `config_warnings` entry, clamped to the floor, not a crash).
* Same delivery path as Phase 4. Unit tests with a short interval/fake clock rather than waiting on real wall
  time.

### Phase 6: Documentation restructuring

* `git mv klorb/usage.md docs/user/usage.md`; update every reference found in "Documentation" above
  (`README.md`, `klorb/README.md`, `.claude/skills/add-cli-flag/SKILL.md`, `docs/specs/model-framework.md`).
* Write `docs/user/hooks.md` per "Documentation" above.
* Run `make lint_docs` from the repo root and fix anything it reports.

### Phase 7: Spec extraction and archival

* Write `docs/specs/hooks-and-events.md` capturing the durable, current-state behavior (config shape, merge
  semantics, hook/event list and scope, handler types, error handling, the `Timer` best-effort caveat) — as a
  frozen-snapshot spec per `AGENTS.md`'s comment/docstring rules, not a history of how this plan arrived there.
* Add one or more ADRs for any decision made along the way that later code or specs will need to point back to
  (e.g. the named-list-concatenate merge variant, `chat` handlers needing `Session.start_turn_if_idle`, the
  best-effort-not-real-cron choice for `Timer`) — check `ls docs/adrs/ | tail -1` for the next number.
* Add a `### Plan 022: Hooks and Events` subsection to `TODO.md` listing this plan's "Future work" items below,
  and remove the `TODO.md` items this plan resolved (the hook env-file item, the `onWorkspaceTrust` item) if not
  already removed during the phase that implemented them.
* `git mv` this plan file to `docs/plans/archive/`.

## Future work

* **onRequestPermission hook.** Deferred entirely — this plan doesn't design its `HookInput`/`HookOutput` fields
  or how it composes with the existing interactive permission-ask flow
  (`klorb.permissions.table.PermissionAskRequired`/`PermissionAskContext`, docs/specs/permissions.md). A real
  design needs to reconcile `HookOutput.permission` (a bare `Verdict`) against the richer `PermissionDecision`
  (`action`+`scope`, `klorb/src/klorb/session/events.py`) a human/UI answer produces.
* **A genuine persistent daemon mode**, so `Timer` can become real cron instead of best-effort. This is a
  process-lifecycle feature in its own right (detach, survive client disconnects, systemd-friendly), not
  naturally scoped inside a hooks/events plan — worth its own plan once there's a concrete need for it.
* **Hot-reloading hook/event config** edited mid-process, without a full restart (today, like every other config
  key, a `hooks`/`events` change requires a fresh `load_process_config()` — e.g. a new session, `/clear`, or
  process restart).
* **Surfacing hook activity in the UI** — a TUI/VSCode view of which hooks fired, what they returned, and
  whether they errored, rather than only `logger.debug()`/`warning()` output. Useful for a user debugging their
  own hook config.
* **Third-party domain blocklisting for `WebFetch` as a `bash`/`classifier` hook** — `TODO.md`'s "Plan 013:
  WebFetch" section already floats "implement as a hook?" for querying external threat lists; once hooks exist,
  revisit that item as a concrete `onToolUse`/`onToolResult` consumer instead of bespoke `WebFetch` code.
* **An explicit turn-interrupt primitive** hooks/events can call directly, rather than the `HookOutput.interrupt`
  boolean needing new wiring on top of a turn's `cancel_event` each time a caller wants it (see "Event output"
  above).
