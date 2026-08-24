# Hooks and Events

klorb lets you attach your own logic to moments in a session's lifecycle (**hooks**) or to
occurrences outside the planned turn-by-turn flow (**events**). Both are configured under the
`hooks`/`events` keys of `klorb-config.json`.

* A **hook** fires at a specific, planned lifecycle moment — a session starting, a tool about to
  run, the agent's turn ending. It can rewrite what happens next, veto it, or leave a message for
  the agent.
* An **event** fires whenever something happens, on its own schedule or trigger — a file changing
  on disk, a timer elapsing, etc. It injects a message into the conversation rather than altering a
  step already in flight.

## Configuration

```json
{
  "hooks": {
    "onSessionStart": [
      {
        "type": "bash",
        "shell": "bin/install-project-dependencies.sh"
      }
    ],
    "onSubmitUserPrompt": [
      {
        "type": "classifier",
        "name": "summarize-prompt",
        "prompt": "Add summary bullet points at the end of the user prompt digesting what the user wrote."
      }
    ],
    "onAgentTurnEnd": [
      {
        "type": "chat",
        "prompt": "The band plays on! Keep going...",
        "filter": { "not": { "contains": "done" } }
      }
    ]
  },
  "events": {
    "FileSystemModified": [
      { "watch": "src", "action": { "type": "chat", "prompt": "Files under src/ changed; take a look." } }
    ],
    "Timer": [
      { "interval_minutes": 30, "action": { "type": "chat", "prompt": "Status check-in." } }
    ]
  }
}
```

If more than one config file in your stack (system, user, project, workspace) declares handlers
for the same hook or event name, all of them run — a later layer's list is appended after
whatever earlier layers already contributed for that name, not replaced. See
`docs/specs/process-and-session-config.md` for how config layers combine in general.

An untrusted workspace's `.klorb/klorb-config.json` never contributes to `hooks`/`events`: that
layer is skipped entirely whenever the workspace isn't trusted, the same as every other key it
could declare.

Every hook/event except `onProcessStart`/`onProcessEnd`/`onSessionStart`/`onSessionEnd` can also
be declared under `sessionDefaults.hooks`/`sessionDefaults.events`, alongside your other
`sessionDefaults` keys — the two sources are combined together, top-level first. Declaring one of
the four process-only hooks under `sessionDefaults` is rejected with a warning; they're only
configurable via the top-level `hooks` key shown above.

### Heritability

Each handler entry can carry its own `isHeritable: true|false`, controlling whether a subagent a
session creates inherits that entry. A hook defaults to `isHeritable: true` (applies tree-wide
unless you opt a specific handler out); an event defaults to `isHeritable: false` (a subagent
starts with no event subscriptions of its own, since a watcher/timer is a standing background
cost, not just a one-off action):

```json
{ "type": "bash", "shell": "...", "isHeritable": false }
```

## Available hooks

Hooks follow the agent through a (mostly-)sequential lifecycle of moments defined below:

| Hook | Fires | Scope |
| --- | --- | --- |
| `onProcessStart` | `bin/klorb` starting up | process |
| `onSessionStart` | a session starting, resuming, or being cleared | process |
| `onSubmitUserPrompt` | a user prompt about to be sent to the agent | whole tree |
| `onRequestPermission` | planned but not yet implemented — see "Future work" | whole tree |
| `onToolUse` | a tool about to run | whole tree |
| `onToolResult` | a tool call's result content | whole tree |
| `onActivateSkill` | a skill about to be activated | whole tree |
| `onSubagentStart` | a subagent's turn starting | parent agent session |
| `onSubagentTurnEnd` | a subagent's turn ending | parent agent session |
| `onAgentTurnEnd` | the agent's turn ending, after its final message | whole tree |
| `onSessionEnd` | a session suspending, being destroyed, or resetting | process |
| `onProcessEnd` | `bin/klorb` exiting | process |

`onProcessStart`/`onProcessEnd`/`onSessionStart`/`onSessionEnd` are process-scoped: they fire
before any session exists, or describe a session's own start/end, and never fire for a subagent
at all. Every other hook fires for whichever session it's actually about — root or subagent — and
consults that session's own configured handlers, tagged with which one fired it.
`onSubagentStart`/`onSubagentTurnEnd` are the one pair that's neither: each fires from the
*parent* session's own handlers, describing the child that's starting/finishing — a handler
configured only on the child doesn't fire for these two.

## Available events

Events differ from Hooks in that they may occur at arbitrary times, like interrupts, but they
interact with Handlers through the same input and output JSON interface.

* **`FileSystemModified`** — watches a workspace-relative file or directory (`watch`; a directory
  is watched recursively) and runs `action` after changes settle, batched over a debounce window.
  A change under any `.git` directory is always ignored. Set `"applyGitignore": true` to also
  skip anything your workspace's own `.gitignore` rules would exclude — handy for watching a
  whole repo root without tripping on build artifacts or other generated files.
* **`Timer`** — runs `action` on a schedule: either `"interval_minutes"` or a `"cron"` string.
  **This is best-effort, not a real scheduler.** klorb has no persistent daemon mode — a `Timer`
  only fires while some klorb process for the workspace is already running for other reasons. A
  fire time that elapses while nothing is running is simply missed, not caught up later. Neither
  an interval nor a cron schedule can fire more often than once every 10 seconds; a tighter
  request is clamped to that floor with a warning.
* **`WorkspaceTrustChanged`** — runs `action` whenever a workspace's trust decision changes
  against an already-live session (the TUI's `>Trust workspace` command, or `_klorb/trustWorkspace`
  over ACP). This is distinct from `onSessionStart`'s own trust fields, which report a session's
  *initial* trust state as part of startup, not a later change.

An event handler is attached to a particular session, not to whichever session your terminal
happens to have focused — it's delivered to the subscribing session's own handler regardless.
Every event you declare in config is a subscription of the root session only, and is not
inherited by a subagent unless you mark it `isHeritable: true` (see "Heritability" above); by
default, a subagent starts with no event subscriptions at all, and only gains one by directly
activating a skill that grants it (see `docs/specs/skills.md`'s `metadata.klorb.events`). The
subagent must still be alive at the moment the event fires for its handler to run — a dormant
subagent (between turns) is woken with a fresh turn, unless doing so would exceed your
subagent-concurrency limits, in which case it's silently skipped rather than delivered.

## Handler types

Every hook config entry, and every event's `action`, is one of three handler types:

* **`bash`** — runs a subprocess, sandboxed the same way an agent-issued `Bash` tool call is.
  Give it `"shell": "a single bash -c string"` or `"command": ["argv", "elements"]` (not both).
  `${home}` and `${workspaceRoot}` are expanded in `command` elements, not in `shell`. The
  handler receives the triggering event as JSON on stdin and must print a `HookOutput`-shaped
  JSON object to stdout; a non-zero exit, a timeout, or invalid output JSON all mean the handler
  contributed nothing. The subprocess also gets a `$KLORB_ENV_FILE` environment
  variable pointing at a file the script can read/write to pass values back and forth without
  putting them in a tool-call argument the model would see.
* **`classifier`** — appeals to a small, fast model with `"prompt"` as its instructions, asking
  for a strict-JSON `HookOutput` reply. Useful for judgment calls that are cheap for a model but
  awkward to script (e.g. "does this look done?").
* **`chat`** — contributes `"prompt"` verbatim as a message. On a hook fired mid-turn (like
  `onAgentTurnEnd`), this starts (or queues) a follow-up turn — see "Chained turns" below.

### Filters

A `filter` on a handler gates whether it runs at all. Each filter clause holds exactly one of:

```json
{ "matches": "exact string" }
{ "pattern": "^regex$" }
{ "contains": "substring" }
{ "any": [ /* filters, OR'd */ ] }
{ "all": [ /* filters, AND'd */ ] }
{ "not": { /* a filter */ } }
```

The subject a filter is checked against depends on the hook: `onSubmitUserPrompt`/
`onAgentTurnEnd`/`onSubagentStart`/`onSubagentTurnEnd` match against the message text, `onToolUse`
matches against the tool name, `onToolResult` matches against the tool's own result content,
`onActivateSkill` matches against the skill's bare name, and the process/session start/end hooks
match against the `reason` (`Startup`, `NewSession`, `SuspendSession`, etc). A handler with no
filter always runs.

## Input and output JSON

A `bash` handler receives the triggering event as JSON on stdin and must print JSON in the
`HookOutput` shape (below) to stdout. A `classifier` handler receives the same input JSON as the
first part of its prompt, and its structured reply is parsed the same way. A `chat` handler
neither receives nor produces this JSON — it just contributes its configured `prompt` as
`message` directly.

### `HookInput` / `EventInput`

```json
{
  "hook": "onToolUse",
  "name": "my-handler-name",
  "args": { "shell": "..." },
  "workspace_root": "/path/to/workspace",

  "reason": "NewSession",
  "message": "the user prompt, agent reply, or subagent output, depending on the hook",
  "tool_name": "Bash",
  "tool_args": { "command": "ls" },
  "tool_result": "the tool's result content",
  "skill_name": "do-thing",
  "skill_namespace": "workspace",
  "is_user_mentioned": true,
  "is_user_activated": false,
  "role": "operator",
  "session_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
  "root_session_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
  "exit_status": 0,
  "workspace_trusted": true,
  "workspace_just_bootstrapped": false,
  "config": { "prompt_input_max_lines": 20 },

  "fs_updates": [
    { "event": "modified", "path": "/path/to/workspace/src/foo.py" }
  ],
  "is_agent_active": false
}
```

Every field is optional except `hook` and `workspace_root` — which fields are actually populated
depends on which hook/event fired:

| Field | Type | Populated for | Notes |
| --- | --- | --- | --- |
| `hook` | string | Always | The hook or event name that fired (`onToolUse`, `FileSystemModified`, ...). |
| `name` | string \| null | Always | The `name` you gave this handler in its config entry, or `null` if you didn't set one. |
| `args` | object | Always | The firing handler's own configured payload: `{"shell": ...}`, `{"command": [...]}`, or `{"prompt": ...}`, whichever it declared. |
| `workspace_root` | string | Always | The workspace's absolute path. |
| `reason` | string \| null | `onProcessStart`/`onProcessEnd`, `onSessionStart`/`onSessionEnd`, `WorkspaceTrustChanged` | Why the hook fired: `Startup`/`Shutdown` (process start/end), `NewSession`/`ResumeSession`/`SuspendSession` (session start/end), `ResetSession` (see "Session reset" below), or `TrustCommand`/`AcpTrustWorkspace` (`WorkspaceTrustChanged`). |
| `message` | string \| null | `onSubmitUserPrompt`, `onAgentTurnEnd`, `onSubagentStart`, `onSubagentTurnEnd` | The user prompt, the agent's reply, or a subagent's prompt/output, depending on which hook fired. |
| `tool_name` | string \| null | `onToolUse`, `onToolResult` | Which tool. |
| `tool_args` | object \| null | `onToolUse`, `onToolResult` | The tool call's arguments. |
| `tool_result` | string \| null | `onToolResult` | The tool call's result content. |
| `skill_name` | string \| null | `onActivateSkill` | The skill about to be activated, by its canonical (directory-basename) identity. |
| `skill_namespace` | string \| null | `onActivateSkill` | The skill's namespace. |
| `is_user_mentioned` | bool \| null | `onActivateSkill` | Whether the current turn's raw prompt referenced this skill by `/<name>` anywhere in it. |
| `is_user_activated` | bool \| null | `onActivateSkill` | Whether the current turn's raw prompt *led* with a `/<name>` reference to this skill. |
| `role` | string \| null | Every hook once a session exists | The relevant (sub)agent role name. |
| `session_id` | string \| null | Every hook once a session exists | The id of the session (root or subagent) that fired this hook; `null` for `onProcessStart`/`onProcessEnd`, since no session exists yet. |
| `root_session_id` | string \| null | Every hook once a session exists | The id of the root session this firing's session descends from — identical to `session_id` for the root session itself. |
| `exit_status` | int \| null | `onProcessEnd` only | The klorb process's own exit status. Read-only — a handler's `HookOutput` can't change it. |
| `workspace_trusted` | bool \| null | `onSessionStart` only | Whether the workspace is trusted. |
| `workspace_just_bootstrapped` | bool \| null | `onSessionStart` only | Whether this firing is what triggered a first-time trust decision. |
| `config` | object \| null | `onProcessStart`, `onSessionStart` | The entire resolved config, as a JSON-dumped `ProcessConfig`. |
| `fs_updates` | array \| null | `FileSystemModified` event only | A debounced batch of `{"event": "created"\|"deleted"\|"modified", "path": "..."}` entries. |
| `is_agent_active` | bool \| null | Every event | Whether the firing session's agent is mid-turn at the moment the event fired. |

### `HookOutput`

```json
{
  "success": true,
  "log": "notice just for the user",
  "tool_args": { "command": "ls -la" },
  "permission": "allow",
  "message": "text to inject into the conversation, or feedback on a denial",
  "tool_result": "a rewrite of the tool's own result content",
  "interrupt": false,
  "reset_session": false
}
```

Every field is optional — omit anything you have no opinion on, and it won't affect the outcome:

| Field | Type | Default | Valid in | Notes |
| --- | --- | --- | --- | --- |
| `success` | bool | `true` | `onSubmitUserPrompt`, `onToolUse`, `onActivateSkill`, `onSubagentStart` | Set `false` to veto the moment: block the turn, the tool call, the activation, or skip the subagent turn. |
| `log` | string \| null | `null` | all | Prints a message in the history |
| `tool_args` | object \| null | `null` | `onToolUse` | Replaces the tool call's arguments before it runs. |
| `permission` | `allow`\|`ask`\|`deny` \| null | `null` | `onToolUse`, `onActivateSkill` | |
| `message` | string \| null | `null` | `onSubmitUserPrompt` (rewrite); `onAgentTurnEnd` / `onSubagentTurnEnd` / events (next prompt); with any `success: false` (denial feedback) | Multi-purpose |
| `tool_result` | string \| null | `null` | `onToolResult` | Replaces the tool call's result content . |
| `interrupt` | bool | `false` | Any hook/event that also sets `message` | Breaks into an in-flight turn immediately rather than waiting for turn end. Only acted on alongside `reset_session` today (see "Session reset" below); elsewhere it's accepted but has no effect. |
| `reset_session` | bool | `false` | `onAgentTurnEnd`, `Timer`/`FileSystemModified`/`WorkspaceTrustChanged` events | Wipes the conversation and starts it over in place (same session id/directory), seeded with `message`. See "Session reset" below. |

If more than one handler runs in a chain for the same firing, each handler's `HookOutput` feeds
into the next handler's input, and the final aggregate is the strictest/most-recent combination of
every handler's opinion: `success` is `false` if any handler said so, `permission` is the
strictest of any handler that gave one, and `message`/`tool_args`/`tool_result` take the last
handler's value that actually set one.

### Chained turns

A `chat` handler on `onAgentTurnEnd`/`onSubagentTurnEnd` queues its message to become the next
turn once the current one ends — the same thing that happens when you queue a message and let the
current turn finish, so it appears the same way: no special marker, just a new turn. Because a
`chat` handler attached to `onAgentTurnEnd` can trigger another `onAgentTurnEnd`, always give it a
`filter` so it stops itself eventually. As a backstop even when a filter is missing or wrong,
klorb caps how many turns in a row can be auto-chained this way (`tools.hooks.maxChainedTurns`,
default `5`; set it to `0` to disable chaining entirely, or a negative number for no cap); once
the cap is hit, further auto-chained turns are refused until a real user-driven turn resets the
count.

An event's `message` (`FileSystemModified`/`Timer`/`WorkspaceTrustChanged`) is queued the same
way if a turn happens to be running when it fires. If nothing is running, the client is woken up
to resubmit it as a fresh turn instead.

### Session reset

`HookOutput.reset_session` wipes the firing session's conversation and starts it over in
place — same session id, same on-disk directory — seeded with `message` as its next turn, as if
you'd run "Clear session" yourself except nothing is actually replaced. For a root session,
config is also reinitialized from the process config's template; a subagent's own config
(role, tools, skills, hooks/events) is left untouched — only its conversation is wiped. Any
live subagents of the session being reset are closed first, and the persistent bash
shell/scratchpad are torn down and recreated fresh. Only `onAgentTurnEnd` and the event hooks
(`Timer`/`FileSystemModified`/`WorkspaceTrustChanged`) act on it. Requires a non-empty
`message`, or the request is dropped with a warning.

When an `onAgentTurnEnd` handler triggers a reset on a root session, `onSessionEnd` is also
dispatched first, with `reason: "ResetSession"` — `onSessionEnd` never applies to a subagent, so
this doesn't happen for one.

A `reset_session` from one of the event hooks can arrive while the firing session's own turn is
still running. Without `interrupt`, that reset is dropped rather than applied underneath the
running turn — it doesn't queue for later, so a `Timer`/`FileSystemModified`/
`WorkspaceTrustChanged` handler that wants a reset to actually happen every time it fires should
set `interrupt: true`. With `interrupt: true`, klorb cancels the in-flight turn, waits for it to
unwind, and then resets — the same outcome as if you'd pressed Escape and submitted a new prompt
yourself.

An `onAgentTurnEnd` reset handler should have a `filter` or be conditional within the handler
script. The reset conversation's own first turn can end and trigger the hook again, and
`max_chained_hook_turns` resets to `0` every reset, so an unfiltered handler can reset indefinitely.

## Error handling

A handler that times out, exits non-zero, or produces output that doesn't parse as `HookOutput`
JSON contributes nothing — it's logged at `warning` and the lifecycle moment or event proceeds as
if that handler weren't there. A hook is a policy overlay, not something that can take down a
session by misbehaving. Likewise, a malformed hook/event config entry (unknown `type`, missing
required field) is skipped at config-load time and surfaced via `klorb show-config`'s warnings,
rather than crashing startup.

## Future work

* An `onRequestPermission` hook, to let a hook weigh in on file/tool permission prompts.
* A genuine persistent daemon mode, so `Timer` can become real scheduling instead of best-effort.
* Hot-reloading `hooks`/`events` config without a full session restart.
* Surfacing hook activity (what fired, what it returned) in the TUI/VSCode UI.
