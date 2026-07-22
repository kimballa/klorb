---
name: create-edit-skill
description: "How to author or edit a klorb skill: pick the right tier directory, escalate privileges for
  it if needed, then create or edit SKILL.md (and any supporting files) with the ordinary file
  tools. Use when the user asks to write, add, change, or fix a skill."
---

# Creating and editing a klorb skill

A skill is a directory whose basename is its `name` (a lower-kebab-case slug), containing at
minimum a `SKILL.md`. There is no `CreateSkill`/`EditSkill` tool: a skill is authored with the
ordinary `CreateFile`/`EditFile`/`ReadFile` tools, preceded by `EscalatePrivileges` whenever the
tier directory is one of the privileged ones those tools are otherwise hard-blocked from
(`.klorb/skills/` and the user tier, but not `.claude/skills/` — see step 3).

## 1. Pick the tier and directory

A skill lives in one of two writable tiers (the third, `internal`, ships inside klorb and is not
authored at runtime):

* **workspace** — a skill specific to this project. Only discoverable when the workspace is
  trusted. Lives in one of two source directories, both sharing the same `workspace` namespace:
  * `${workspaceRoot}/.klorb/skills/<name>/` — klorb's own convention.
  * `${workspaceRoot}/.claude/skills/<name>/` — Claude Code's convention, discovered as a second
    source for `workspace` skills only when the `compatibility.claudeSkills` flag is on.
* **user** — `$KLORB_DATA_DIR/skills/<name>/` (default `~/.local/share/klorb/skills/`) — a skill
  available to you across every workspace.

Before deciding, check the `config.compatibility.claudeSkills` flag in the system prompt's own
`## Metadata` section (it's always there — no file access needed). If that flag is off,
`.claude/skills/` isn't discovered at all, so putting a new skill there would be silently inert —
just use `.klorb/skills/` without asking.

**When creating a *new* workspace-tier skill** and `compatibility.claudeSkills` is on, check
whether `${workspaceRoot}/.claude/skills/` already exists. If it does, ask the user whether to
store the new skill there or under `.klorb/skills/` — the repo has already committed to a
convention by having that directory, and only the user knows whether to stay consistent with it or
start using klorb's own convention going forward. If `.claude/skills/` does not exist, just use
`.klorb/skills/` without asking. When *editing* an existing skill, use whichever directory it
already lives in — there's nothing to ask.

The directory basename **is** the skill's canonical `name` — the identity every `skillRules`
approval decision is keyed on. Use a lower-kebab-case slug with no path separators or `:`.

## 2. `SKILL.md` shape

`SKILL.md` MUST open with YAML frontmatter carrying `name` and `description`, then a markdown body of
the actual instructions:

```markdown
---
name: my-skill-name
description: A sentence or two saying what this skill does and when to use it. This exact text is what gets listed for the model, so keep it short and specific.
---

<the skill's actual instructions>
```

The frontmatter `name` should match the directory basename exactly — klorb logs a warning if they
disagree, and the directory basename always wins as the canonical name (the frontmatter `name`
still works as an alias a user can type, but klorb itself never resolves through it). `description`
is the other frontmatter field klorb reads; keep it to a sentence or two — it's what the
available-skills list shows. Supporting files (reference material, templates, scripts) go alongside
`SKILL.md` in the same directory; the model reaches them with `ReadSkillFile` once your instructions
point it at them.

## 3. Escalate privileges for the tier

The file tools hard-block writes into privileged directories, so escalate first — except
`.claude/skills/`, which isn't privileged:

* **`.klorb/skills/...`:** call `EscalatePrivileges(scope="workspace")`. In a trusted workspace
  this lifts the `.klorb/` block, and the trusted workspace's own `writeDirs.allow` already covers
  `.klorb/skills/`, so no further approval is needed.
* **`.claude/skills/...`:** not inside `.klorb/`, so it's an ordinary project directory gated by
  the regular `writeDirs` rules, not the hard block. No `EscalatePrivileges` call — write it with
  the plain file tools like any other workspace file, subject to the normal ask/allow flow.
* **user tier:** call `EscalatePrivileges(scope="homedir")`. Approving it grants session-level
  read/write access to `$KLORB_DATA_DIR` (and the other klorb home directories) so the file tools
  can write beneath `$KLORB_DATA_DIR/skills/`.

## 4. Create or edit the files

With the escalation approved, use the ordinary file tools against the full path:

* `CreateFile` a new `<tier-dir>/skills/<name>/SKILL.md` (and any supporting files).
* `ReadFile` / `EditFile` an existing skill's files to change them.

Write the frontmatter exactly as in step 2 — a malformed or missing `description` is treated as an
empty description (the skill is still discoverable, it just lists blank), so double-check it.

## 5. Verify it's discoverable

A newly-added skill won't appear in the standing available-skills list until a fresh session
(`/clear`), since that list is compiled once per session. To confirm the skill is discoverable
right now, use `SearchSkills` with a keyword from its name or body, or `ActivateSkill` it directly
by its `(namespace, name)`. The first activation of a new skill may prompt for approval unless a
`skillRules.allow` entry already covers it.
