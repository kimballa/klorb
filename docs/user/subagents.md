# Subagents

A **subagent** is a helper session your agent (the "operator") spawns to go do a bounded piece of
work — research a question, review a diff, carry out a plan — and report back. It's not a
separate program you launch yourself; your agent decides when a subagent would help and creates
one on the fly. Subagents are how klorb splits a big task across more than one agent, or keeps a
side investigation from bloating your main conversation's context.

## What you'll see

You don't have to do anything for subagents to work — your agent creates, messages, and collects
output from them on its own. What you *will* notice:

* New rows appear in the **subagents panel** (press `Ctrl+G` in the TUI) as your agent spawns
  helpers. Each row shows the subagent's address (`1.1`, `1.2`, `1.3`, ... — numbered in creation
  order under the root session, `1`) and a short title.
* Selecting a subagent's row shows its own transcript, separate from your main session's history.
  A status line at the bottom tells you whether it's still working, finished, or was interrupted.
* You can type into the prompt input while a subagent is selected — your message goes straight to
  that subagent, not your main session. Use this to redirect a subagent that's gone off track
  without waiting for your main agent to notice.
* `Escape`/`Ctrl+C` while a subagent is selected interrupts just that subagent, leaving your main
  session's work untouched.

## Available roles

Every subagent runs as one of a fixed set of **roles**, each with its own default model, its own
slice of your tools/skills, and its own job description. Which roles a given agent may launch is
itself restricted — see "Configuring roles" below.

| Role | What it's for |
| --- | --- |
| `operator` | The default top-level role — the one your own session runs as. Owns a task end to end: researches, plans, writes code, runs tests, and may delegate to any other role below. |
| `explorer` | Read-only research: answer a bounded question by reading code, prior decisions, or the web, then report back. Can't edit anything. |
| `reviewer` | Audits a completed change and reports findings — bugs, missing tests, design concerns — without taking over the work itself. |
| `planner` | Turns a task into a written implementation plan, without writing the implementation. |
| `implementer` | Carries out a plan someone else (you, or a Planner) already wrote. |
| `pair_programmer` | An ongoing collaborator, not a one-shot specialist: reviews your agent's plan before any code is written, then keeps watching its edits and todo list for the rest of the session, sending feedback as it goes. Started via the `/pair-programming` skill. |

A role's own system prompt is what actually tells it how to behave day to day — see
"Role-specific system prompts" below for how to read one.

## Configuring roles: `agents.json`

Every role's capability policy — which tools/skills it starts with, whether it may launch further
subagents, and what background hooks/events it comes with — is defined in one file klorb ships,
`agents.json`. There's no user- or project-level override for it today (unlike skills or system
prompts); changing what a role can do means editing the packaged file. One entry looks like this:

```json
{
  "name": "explorer",
  "default_model": "klorb-default/fast",
  "restrict_to": {
    "tools": ["ReadFile", "Grep", "..."],
    "tool_categories": ["..."],
    "skills": [],
    "subagent_roles": ["explorer"],
    "enforce_readonly_tools": true
  },
  "allow_subagents": true,
  "agent_capabilities": {
    "accepts_tasks": false,
    "assigns_tasks": false,
    "see_group_tasks": false,
    "send_messages": false
  },
  "hooks": {},
  "events": {
    "FileSystemModified": [
      { "watch": ".", "action": { "type": "chat", "prompt": "..." } }
    ]
  }
}
```

* **`default_model`** — which model a subagent of this role uses unless the call that created it
  overrides it. Usually one of the `klorb-default/fast`/`normal`/`heavy`/`current` placeholders,
  resolved against your own `klorb-config.json` model settings rather than a literal model name.
* **`restrict_to`** — narrows what a subagent of this role inherits from *whichever agent creates
  it*. A subagent can never end up with more than its creator already has, no matter what this
  section says — it only ever narrows, never widens. Leaving a field unset means "inherit
  everything the creator has"; an empty list means "inherit nothing."
  * `tools` / `tool_categories` — which tools (by name, or by category) the role keeps.
  * `skills` — which skills (by fully-qualified `namespace:name`) the role keeps.
  * `subagent_roles` — which roles a subagent of this role may itself launch.
  * `enforce_readonly_tools` — when `true`, clamps the tool set down to read-only tools only
    (what makes Explorer safe to hand a broad research question).
* **`allow_subagents`** — whether a subagent of this role may launch subagents of its own at all.
* **`agent_capabilities`** — separate from tool access, these gate a few specific behaviors, all
  off by default: `accepts_tasks` (may hold a tracked task as its own), `assigns_tasks` (may
  create a tracked task assigned to a *different* agent), `see_group_tasks` (may see every task
  the whole group is tracking, not just its own), `send_messages` (may message another agent in
  the group directly — any role may still *receive* a message regardless of this flag).
* **`hooks`** / **`events`** — let a role come with its own background behavior built in, in the
  same shape as the hooks and events blocks of `klorb-config.json` (see `docs/user/hooks.md`).
  Every subagent created as this role picks these up automatically.

## Role-specific system prompts

A role's actual day-to-day instructions live in a markdown file, not in `agents.json` — one file
per role, under `system_prompts.d/roles/<role>/default.md`, concatenated onto klorb's own
role-agnostic default prompt. To see exactly what a given role's agent is told, run:

```console
klorb system-prompt --role explorer
```

This prints the fully resolved system prompt (the shared default plus that role's own addendum),
the tool definitions it would receive, and a token-count breakdown. Add `--model` to see a
model-specific prompt tier if one exists for that role. See `klorb system-prompt --help`, or
`docs/user/usage.md`'s `COMMANDS` section, for the full flag list.
