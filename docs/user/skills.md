# Skills

A **skill** is a bundle of instructions — and, optionally, supporting files — that teaches the
agent how to do one bounded, reusable task: write a design doc, review a change, pair-program with
you. Skills are how klorb (or you) hand the agent task-specific know-how without bloating every
session's system prompt.

Invoke one explicitly by typing `/<skill-name>` at the start of a message, or just describe what
you want — klorb keeps a one-line description of every skill in context and activates a relevant
one on its own when it judges it applicable.

## Where klorb looks for skills

Skills are discovered from three locations, checked in this order (most specific wins on a name
collision):

1. **User** — `~/.local/share/klorb/skills/<name>/SKILL.md`. Anything you add here follows you
   across every project.
2. **Workspace** — `.klorb/skills/<name>/SKILL.md` in the current project. Only used once you've
   trusted the workspace.
3. **Internal** — shipped inside klorb itself. This is where the skills described below live.

Each is a directory named after the skill (`pair-programming/`, not `Pair Programming/`)
containing a `SKILL.md` file: a short YAML header (`name`, `description`) followed by the actual
instructions in markdown. See `/create-edit-skill` below if you want to write your own.

If a project already has Claude-Code-style skills under `.claude/skills/`, turn on
`compatibility.claudeSkills` in `klorb-config.json` (or just run `/claude-compatibility`) and
klorb will read those too, alongside `.klorb/skills/`.

## What's different about a klorb skill

A skill written for klorb can do a couple of things a plain Claude Code skill can't, via a
`metadata.klorb` block in its frontmatter:

```yaml
---
name: my-skill
description: what it's for and when to use it
metadata:
  klorb:
    bashCommands:
      - ["git", "status"]
    events:
      FileSystemModified:
        - watch: "."
          action: { type: chat, prompt: "Something changed -- take a look." }
---
```

* **`bashCommands`** pre-approves specific commands the skill's own instructions rely on, so
  you're not asked to approve them the first time the agent runs one.
* **`hooks`/`events`** let activating the skill subscribe the current session to a lifecycle hook
  or a standing event — most notably `FileSystemModified`, which watches files on disk and wakes
  the agent when they change (see [Hooks and events](hooks.md)). This is how `/pair-programming`
  (below) gives its reviewing agent a live view of your edits.
* **`disable-model-invocation`** marks a skill as reachable only by typing its exact `/name` — the
  agent won't stumble into it on its own. A few of klorb's internal skills use this for narrow
  setup steps you'll never need to invoke directly.

Everything else about a `SKILL.md` file — the `name`/`description` frontmatter, the markdown
body — follows the same format Claude Code skills use, so existing skill-authoring habits transfer
directly.

## Notable built-in skills

klorb ships several skills you can invoke by name. A few worth knowing about:

* **`/pair-programming`** — work through a large or uncertain task alongside a second agent: agree
  on the design together first, then let it watch your edits and your todo list as you implement,
  trading feedback over chat as you go.
* **`/code-review`** — review the working diff, the latest commit, a branch, or a GitHub PR for
  bugs, architecture fit, and (if one exists) conformance to its plan or spec.
* **`/write-plan`** — turn a feature, refactor, or other engineering task into a written
  implementation plan before code gets written.
* **`/create-edit-skill`** — author or edit a skill of your own, in the workspace or user tier.
* **`/claude-compatibility`** — turn on support for a project's existing `.claude/skills/`/
  `CLAUDE.md` files.

klorb also ships a handful of narrower skills the agent activates on its own when they're relevant
(debugging technique, multithreading pitfalls, and similar know-how). You don't need to remember
their names — they show up in the agent's behavior, not in your prompt.
