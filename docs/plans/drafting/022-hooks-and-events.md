
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

**security:** An untrusted workspace config cannot add any hooks or events.

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

#### type=classifier

We run the same fast classifier that we use for session naming. We feed it the prompt that the user supplied, as
well as information about the hook-generating event. We give it a brief system prompt explaining it's intended
to help run a hook, and that the user msg will include json data about the event plus a user prompt. Then we
put the json first, then the user prompt. We also give the classifier a specific json output shape that it must
generate as its response.

#### type=chat

This causes a new "user message" to be sent back to the agent to continue the conversation autonomously.
Especially if used at the end of the agent turn, this should be used with a filter so that it does not
run in an infinite loop.

### Filters

Filter clauses can go inside a hook config, which determine if that particular hook config is valid to run.

Each filter should contain exactly one of the fields defined in this example payload:

```json
{
  "matches": "literal string",
  "pattern": "^some regex$",
  "any": [ /* list of filters, any one of which must eval to true for this to eval to true */ ],
  "all": [ /* list of filters, all of which must eval to true for this to eval to true */ ],
  "not": { /* a filter */ }
}
```

### Hook handler execution

* Bash commands are run in an isolated one-off session, cwd=workspaceRoot. Same bubblewrap as the agent cmds get.
* Multiple valid handlers for an event are run in an order of the system's choosing and are not
  guaranteed to be sequential or parallel in any particular way.
* If the hook is determining whether something succeeds or not, we use the strictest outcome
  * any "reject" is a rejection no matter if others accept.
  * allow/ask/deny fail closed to "any deny" first, then "any ask"; allow only if all allow.
  * if hooks are silent about a judgment that means the default judgment takes hold. i.e., if we would ordinarily
    ask the user for permission, just ask for permission. if we would ordinarily auto-deny (or
    auto-accept) b/c no user is present, then we auto-deny / auto-accept depending on the system
    mode.

### Available hooks

* onProcessStart -- when bin/klorb starts up in this workspace (matches refer to `event`)
* onSessionStart -- when a session is started, resumed, or cleared (matches refer to `event`)

* onSubmitUserPrompt -- when a user prompt is about to be sent to the agent (matches are on 'message')
* onRequestPermission -- when the agent needs to ask permission for file/dir access or tool/skill use, etc.
* onToolUse -- when the agent is about to run a tool. preprocesses the tool inputs (matches are on tool_name)
* onToolResult -- preprocesses the tool use result before returning it to agent. (matches are on message)
* onSubagentStart -- like onSubmitUserPrompt but when we kick off the subagent.
* onSubagentTurnEnd -- like onAgentTurnEnd.
* onAgentTurnEnd -- after the agent has printed its final msg and declared its turn over.
* onSessionEnd -- when we are suspending or destroying a session (matches: event)
* onProcessEnd -- when bin/klorb is exiting. (matches: event)

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

  // TODO FILL IN PERMISSION-ASK SCHEMA
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
      "permission denial."
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
        "cron": "30 2,4,6,8 * *", // at various early hours of the day.
        "action": /* ... */ }
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

### FileSystemModified event

* Runs the specified action when the filesystem is updated. We put an inotify watcher on this, attached
  to the root session. (All "chat" operations that could inject a new chat are always directed at )

### Timer event

* Runs the specified action either on an interval (measured in minutes; accepts a float, but shall be no
  more frequent than once every 10 seconds) or on a cron timer.

### Event input

* looks basically like `HookInput`
  * fs events should also include a bunch of `"fs_updates": [ UpdateObj ]`
    * UpdateObj has a field for `event` (created, deleted, modified) and another field for `path`.
    * fs events are debounced by 10 seconds. All inotify events that occur in a burst are delivered
      as one event.

### Event output

Same schema as HookOutput

* If the event creates a `message` then it will be passed to the agent
* If the agent is running, it's a queued user interjection
* If the agent is not running, we start a new user turn; add a prefix that says:
  "An event has resumed this conversation:\n(event handler msg here)"
* If the agent keeps running on its own on a long think, this might take a while. Add an `interrupt` boolean
  to the HookOutput schema. If interrupt is true, it's like when a user queues a message for the agent
  and then hits ^C to disrupt what the agent is currently doing, so the queued message breaks into the
  conversation.

## Documentation

Start a `docs/user/` dir. Move usage.md into there. Add a hooks.md that details this in a way that a technical
user can follow, but not buried in with 'docs/specs' with all the how-its-implemented stuff.
