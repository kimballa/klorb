---
name: write-software-factory-queue
description: >
  Scan TODO.md for items tagged `#agent` and promote them into docs/plans/auto/ — either
  as bullets in queue.md (small tasks) or as standalone files (larger tasks) — with enough
  context that an autonomous agent can execute them without asking questions. Use when asked
  to "write the auto queue", "promote agent tasks", "populate the software factory queue",
  or similar.
---

# Write software-factory queue

This skill reads `TODO.md`, finds every item tagged `#agent`, and writes them into
`docs/plans/auto/` in the shape the `enable-software-factory` skill expects: small tasks as
top-level bullets in `queue.md`, larger tasks as standalone `.md` files.

## 1. Read TODO.md and collect `#agent` items

Read the full `TODO.md`. For every bullet (line starting with `* ` or `- `) that contains
the literal string `#agent`, record:

- **The bullet text** and any indented sub-bullets (child lines indented under it).
- **The section path** — every `##`/`###` heading above the bullet, outermost first.
  For example, an item under `## TUI` → `### Bugs` has section path `TUI > Bugs`.
  An item under `## Agent / Harness` → `### Feature backlog` has section path
  `Agent / Harness > Feature backlog`.
- **Whether the item is in a "Bugs" section.**

If no `#agent` items exist, report that and stop.

## 2. Enrich each item for autonomous execution

A bare bullet ripped from TODO.md is often ambiguous — context lives in the surrounding
headings, sibling items, and prose. For each item, write a self-contained task description
that an agent with no access to TODO.md could implement without asking questions.

Rules for enrichment:

1. **Prefix with the component.** Start the task with the component name derived from the
   section path: `[Harness]`, `[TUI]`, `[VSCode plugin]`, etc. If the section path already
   makes the component obvious (e.g. `## TUI`), the prefix still helps because queue.md
   mixes all components together.
2. **Prefix with `[Bug]` if from a Bugs section.** Place it before the component:
   `[Bug][TUI] ...`.
3. **Spell out implicit nouns.** If the bullet says "it should also handle X", replace "it"
   with the actual class/tool/feature name. If the bullet references another item by
   proximity ("same as above" or an implicit subject from a parent heading), spell it out.
4. **Carry forward essential context from parent bullets or headings.** If the item is a
   sub-bullet of a larger feature description, fold the parent's key context into the task
   text so it stands alone. Don't include the entire parent if only one sentence matters.
5. **Keep referenced file paths, spec links, and class names.** These are precision, not
   clutter.
6. **Drop meta-commentary.** Phrases like "(low priority)", editorial questions
   ("How tall is that, exactly?"), and parenthetical speculation ("this may not be fixable")
   should be removed or rephrased as a concrete acceptance criterion where possible.
7. **One task per item.** If a single `#agent` bullet contains multiple distinct
   work items joined by "also" or semicolons, split them into separate tasks.

The enriched text is what gets written to the queue or standalone file.

## 3. Classify: small (queue.md) vs. large (standalone file)

A task is **small** if all of the following are true:

- It can be described in 1–3 sentences after enrichment.
- It touches one component or file area.
- It has no open design questions or external dependencies (e.g. "once X is merged").
- It doesn't require a spec or ADR to be written first.

Everything else is **large**.

When in doubt, treat as large — a standalone file is never wrong, but an underspecified
queue bullet wastes an autonomous agent's time.

## 4. Write small tasks to queue.md

Read `docs/plans/auto/queue.md`. Append each small task as a new top-level bullet
(starting with `- `) at the end of the file, after the existing prose header. Preserve the
file's existing header and format.

Example appended line:

```
- [Bug][Harness] Apply SecretDetector to BashTool stderr/stdout output so secrets logged by subprocesses are masked before reaching the agent's context.
```

If queue.md already contains a bullet whose text is a substring match of a task you're about
to add (after stripping the prefix), skip it — don't create duplicates.

## 5. Write large tasks to standalone files

For each large task, create a new file `docs/plans/auto/<slug>.md` where `<slug>` is a
short, descriptive, lower-kebab-case name (under 50 chars). Content:

```markdown
# <Title derived from the task>

<Component context — one line saying which part of the codebase this touches>

## Task

<The enriched task description>

## Context

<Any additional context carried from TODO.md that didn't fit cleanly into the one-line
task: referenced specs, related items, design constraints. Keep it to a few bullets.>
```

If a file with the same slug already exists, read it. If it covers the same task, skip it.
If it covers a different task, append a numeric suffix (`-2`, `-3`, ...).

## 6. Do NOT remove items from TODO.md

This skill only *promotes* items into the auto queue. It does not remove them from TODO.md.
Removal happens when the task is actually implemented (by the `auto-agent-task` or
`enable-software-factory` skill). This keeps TODO.md as the canonical backlog until work is
done.

## 7. Report

Summarize what was written:

- How many `#agent` items were found.
- How many went to queue.md (list the bullets).
- How many got standalone files (list the filenames).
- Any items that were skipped as duplicates.
- Any items that were split from a single bullet into multiple tasks.
