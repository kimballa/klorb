---
name: write-plan
description: >
  Write a detailed implementation plan for a feature, refactor, or other engineering task.
  Use when the user asks you to plan, scope, or design an implementation — or when a task
  is too large or ambiguous to start coding without a written plan first.
---

# Writing an implementation plan

Your job is to produce a clear, detailed, human-readable and agent-readable planning document.
You do not implement the plan. You research, decide, and write.

## 1. Understand the request

Before writing anything, make sure you understand what is being asked:

* Restate the goal in your own words. If anything is ambiguous, use `AskUserQuestions` to
  clarify before proceeding.
* Identify the scope: is this a new feature, a refactor, a bug fix, a platform change? What
  is in scope and what is explicitly out of scope?
* Note any constraints the user mentioned (deadlines, dependencies, "don't touch X", etc.).

## 2. Research the codebase

You cannot plan against an imagined codebase. Read the relevant code, specs, decisions,
and docs before writing the plan.

* **Launch Explorer subagents** (`role="explorer"`) to investigate areas you don't yet
  understand. Give each a bounded question: "How does X work?", "Where is Y defined?", "What
  patterns does Z follow?". Launch several in parallel when the questions are independent.
* **Read existing specs and decision records** that relate to the
  area you're planning.
* **Check for existing plans** to avoid duplicating or conflicting with planned work.
  Be mindful that the implementation may have drifted since those plans were implemented.
* **Understand the project's conventions**: language, patterns, module layout, testing
  approach, naming. Read `AGENTS.md` and any contributor guides. The plan must fit the
  codebase it targets.

## 3. Decide on an approach

With the research done, weigh the viable approaches:

* There is usually more than one way to solve a problem. Identify 2–3 candidates if they
  exist.
* For each, note the tradeoffs: complexity, risk, how well it fits existing patterns, what
  it enables or blocks in the future.
* Commit to one. State why. Note what evidence would change your mind (so an implementer
  knows when to stop and reassess).
* If the decision is genuinely the user's to make (irreversible, taste-dependent, implies
  a large scope increase, or conflicting requirements), ask via `AskUserQuestions` — but
  bundle all such questions into as few rounds as possible (ideally just one).

## 4. Write the plan

Check the workspace for an existing plan location and conventions:

* If the project has a `/plans/` directory (or similar), read its README or conventions
  file and follow them. Follow any subdirectory structure that implies a workflow of drafts
  vs finished documents. Follow local file-naming conventions.
* If the project has directoriers for documentation but nothing plan-specific, create one
  at a location that fits the project's doc structure. If the project has no doc directory
  at all, writing the plan to a file in the workspace root is fine.

### Plan structure

A plan should contain, as applicable:

1. **Summary** — one paragraph stating what this plan accomplishes and why.
2. **Background / Motivation** — why this work is needed. Reference specs, ADRs, or user
   requests. Keep it brief; the reader can follow links.
3. **Approach** — the chosen technical approach, explained at a level an implementer can
   follow without re-deriving every decision. Include:
   * What changes to which files/modules/packages.
   * New classes, functions, config keys, or data structures introduced.
   * How the change integrates with existing code (what calls what, what reads what).
   * Data model changes, if any.
4. **Stages / Phases** — break the work into ordered, independently-verifiable stages.
   Each stage should be small enough that one agent (or one focused session) can implement
   and verify it. For each stage:
   * A short heading describing the stage.
   * What files are created or modified.
   * What tests are added or updated.
   * Any dependencies on earlier stages.
   * Give concrete tasks to perform, objectives to fulfill, or conditions for how to
     verify the phase is completely implemented.
5. **Testing strategy** — how the implementer should verify the work: unit tests, integration
   tests, manual testing, linter/typecheck commands.
6. **Risks and open questions** — anything uncertain, any assumption the plan depends on,
   any decision that might need revisiting. Flag these explicitly so they don't silently
   become blockers.
7. **Future work** — ideas that are out of scope for this plan but worth tracking, possibly
   in a project-wide `TODO` file or other task tracker post-implementation.

### Writing quality

* Write for an agent reader: be precise, cite file paths, name classes and methods. An
  implementer should be able to follow the plan without guessing.
* Also write for a human reader: the user will review and approve the plan before
  implementation. Keep prose clear and avoid unnecessary jargon.
* Don't pad. If a section doesn't apply, omit it rather than writing "N/A".
* Don't narrate your research process in the plan. The plan describes the destination, not
  the journey. Do not write about paths not traveled.

## 5. Present the plan and get feedback

After writing the plan file:

1. Inform the user where the plan file is (full path).
2. Use `AskUserQuestions` to ask whether the plan is acceptable, needs revisions, or should
   be abandoned. Offer concrete options (e.g. "Looks good", "Needs changes — I'll describe
   what", "Abandon this approach").
3. If the user requests changes, revise the plan file and ask again.
4. Once approved, the plan is ready. Do not implement it.

## 6. If launched as a subagent

If you are launched as a subagent (not the top-level session), you may create tasks for
implementing the plan's stages. Use `TodoCreate` with `assign_to="all"` so the tasks go
back to the operator to perform. Each task should reference the plan file and the specific
stage it covers.

Do not accept tasks yourself (`TodoNext` is not for you). Your job ends when the plan is
written and approved.
