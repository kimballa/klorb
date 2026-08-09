# Hooks and Events

klorb lets you attach your own logic to moments in a session's lifecycle (**hooks**) or to
occurrences outside the planned turn-by-turn flow (**events**). Both are configured under the
`hooks`/`events` keys of `klorb-config.json`, and both are inert by default: an empty config
changes nothing.

* A **hook** fires at a specific, planned lifecycle moment — a session starting, a tool about to
  run, the agent's turn ending. It can rewrite what happens next, veto it, or leave a message for
  the agent.
* An **event** fires whenever something happens, on its own schedule or trigger — a file changing
  on disk, a timer elapsing, workspace trust changing. It injects a message into the conversation
  rather than altering a step already in flight.

## Configuration

```json
{
  "hooks": {
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

## Handler types

Every hook config entry, and every event's `action`, is one of three handler types:

* **`bash`** — runs a subprocess, sandboxed the same way an agent-issued `Bash` tool call is.
  Give it `"shell": "a single bash -c string"` or `"command": ["argv", "elements"]` (not both).
  `${home}` and `${workspaceRoot}` are expanded in `command` elements, not in `shell`. The
  handler receives the triggering event as JSON on stdin and must print a `HookOutput`-shaped
  JSON object to stdout; a non-zero exit, a timeout, or invalid output JSON all mean the handler
  contributed nothing (not a crash). The subprocess also gets a `KLORB_HOOK_ENV_FILE` environment
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
`onAgentTurnEnd`/`onToolResult`/`onSubagentStart`/`onSubagentTurnEnd` match against the message
text, `onToolUse` matches against the tool name, and the process/session start/end hooks match
against the `event` name (`Startup`, `NewSession`, `DestroySession`, etc). A handler with no
filter always runs.

### Chained turns

A `chat` handler (or an event's `message`) that fires while no turn is running starts a new one;
if a turn is already running, it's queued as an interjection instead — the same thing that
happens when you queue a message and let the current turn finish. Because a `chat` handler
attached to `onAgentTurnEnd` can trigger another `onAgentTurnEnd`, always give it a `filter` so it
stops itself eventually. As a backstop even when a filter is missing or wrong, klorb caps how many
turns in a row can be auto-started this way (`tools.hooks.maxChainedTurns`, default `5`); once the
cap is hit, further auto-chained turns are refused until a real user- or tool-driven turn resets
the count.

## Available hooks

| Hook | Fires | Scope |
| --- | --- | --- |
| `onProcessStart` | `bin/klorb` starting up | process |
| `onSessionStart` | a session starting, resuming, or being cleared, once workspace trust is settled | root session |
| `onSubmitUserPrompt` | a user prompt about to be sent to the agent | root session |
| `onToolUse` | a tool about to run — can rewrite `tool_args` or veto the call | whole tree |
| `onToolResult` | a tool's result, before it's returned to the agent | whole tree |
| `onSubagentStart` | a subagent's turn kicking off | that subagent |
| `onSubagentTurnEnd` | a subagent's turn ending | that subagent |
| `onAgentTurnEnd` | the agent's turn ending, after its final message | root session |
| `onSessionEnd` | a session suspending or being destroyed | root session |
| `onProcessEnd` | `bin/klorb` exiting | process |

`onToolUse`/`onToolResult` fire for every session in the tree — root or subagent — tagged with
which one fired it. Every other hook above is scoped to either the root session only, or (for the
subagent pair) to the firing subagent only; it never fires for the other side.

`onRequestPermission` is planned but not yet implemented — see "Future work" below.

## Available events

* **`FileSystemModified`** — watches a workspace-relative file or directory (`watch`; a directory
  is watched recursively) and runs `action` after changes settle, batched over a debounce window.
  Always targets the root session's conversation, regardless of which session (root or a
  subagent) happens to be active when the change is noticed.
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
