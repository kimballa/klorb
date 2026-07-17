---
description: >
  How to author or edit a klorb skill: pick the right tier directory, escalate privileges for
  it, then create or edit SKILL.md (and any supporting files) with the ordinary file tools.
  Use when the user asks to write, add, change, or fix a skill.
---

# Creating and editing a klorb skill

A skill is a directory whose basename is its `name` (a lower-kebab-case slug), containing at
minimum a `SKILL.md`. There is no `CreateSkill`/`EditSkill` tool: a skill is authored with the
ordinary `EscalatePrivileges` + `CreateFile`/`EditFile`/`ReadFile` tools, because a skill
directory lives inside a privileged directory the file tools are otherwise hard-blocked from.

## 1. Pick the tier and directory

A skill lives in one of two writable tiers (the third, `internal`, ships inside klorb and is not
authored at runtime):

* **workspace** — `${workspaceRoot}/.klorb/skills/<name>/` — a skill specific to this project.
  Only discoverable when the workspace is trusted.
* **user** — `$KLORB_DATA_DIR/skills/<name>/` (default `~/.local/share/klorb/skills/`) — a skill
  available to you across every workspace.

The directory basename **is** the skill's `name`; there is no `name` field in the frontmatter to
keep in sync with it. Use a lower-kebab-case slug with no path separators.

## 2. `SKILL.md` shape

`SKILL.md` opens with YAML frontmatter carrying a single field, `description`, then a markdown
body of the actual instructions:

```markdown
---
description: >
  A sentence or two saying what this skill does and when to use it. This exact text is what
  gets listed for the model, so keep it short and specific.
---

<the skill's actual instructions>
```

`description` is the only frontmatter field klorb reads. Keep it to a sentence or two — it's what
the available-skills list shows. Supporting files (reference material, templates, scripts) go
alongside `SKILL.md` in the same directory; the model reaches them with `ReadSkillFile` once your
instructions point it at them.

## 3. Escalate privileges for the tier

The file tools hard-block writes into privileged directories, so escalate first:

* **workspace tier:** call `EscalatePrivileges(scope="workspace")`. In a trusted workspace this
  lifts the `.klorb/` block, and the trusted workspace's own `writeDirs.allow` already covers
  `.klorb/skills/`, so no further approval is needed.
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
