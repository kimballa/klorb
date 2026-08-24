---
name: pair-programming
description: >
  Work through a large or architecturally uncertain task together with a Pair Programmer
  subagent: agree on the design before writing any code, then keep it watching your edits and
  your todo list as you implement, trading feedback and questions over SendMessage as you go.
  Skip it for a small, already well-scoped change.
---

# Pair programming with a second agent

## When to use this

Use `/pair-programming` when a task is large, architecturally uncertain, or high-stakes enough
that a second opinion on the design -- and an ongoing reviewer watching your edits -- is worth the
overhead. Skip it for a small, well-scoped change; spawning a pairing partner for a one-line fix
just adds noise on both sides.

## Starting a pairing session

Call `CreateSubagent` with `role="pair_programmer"` and an `initial_message` that starts with the
literal text `/pair-programming-child` -- this is what arms the pairer's live file watch on its
very first turn -- followed by your proposed plan or architecture:

```python
CreateSubagent(
    role="pair_programmer",
    session_title="Pairing: <short task description>",
    initial_message=(
        "/pair-programming-child\n\n"
        "I'm about to <what you're building>. My plan: <the architecture/approach>. "
        "<Any open questions or tradeoffs you're unsure about.>"
    ),
)
```

## Phase 1: agree on the design

Share your actual plan, not just a task summary -- the pairer can only push back on what you tell
it. Expect questions and pushback back over `SendMessage`; take them seriously and refine the plan
together. Don't start editing files until the pairer's satisfied with the approach, or you've
explicitly told it you're proceeding despite a disagreement -- you hold the keyboard, but use good
judgment about when to actually override it.

## Phase 2: implementing together

Once you start writing code, keep sending the pairer short updates on what you're doing and why as
you go -- it can't read your mind between edits. Expect unsolicited `SendMessage` interjections
about the edits it's noticed (it's watching the workspace for file changes) and about your todo
list (it can see everything you're tracking, and may add items or comment on ones it thinks are
incomplete). Check `GetMessages` if you're not sure you've seen everything it's sent. Weigh what
it's flagged before declaring a todo item, or the whole task, done.
