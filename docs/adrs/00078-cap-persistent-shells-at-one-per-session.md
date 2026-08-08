# At most one session-scoped persistent shell exists per `Session`, for now

* Date: 2026-07-08 19:00
* Question: `shell_lifetime="session"` lets the model keep one bash process alive across several
  `Bash` calls. `TODO.md`'s original backlog item explicitly raised an open question alongside
  the core ask: "think ahead to a world where we let klorb run multiple commands in parallel...
  can the agent have multiple session-durable shells? In which case, how does it know which is
  which, and assign various commands to them?" Should this first version support more than one
  concurrent persistent shell per `Session`, addressed by some identifier the model passes?
* Answer: No — exactly one persistent shell slot per `Session`, stored at the single well-known
  key `session.tool_state["Bash"]["persistent_shell"]`. `shell_lifetime="session"` reuses it if
  alive, or creates it if absent/dead; `shell_lifetime="new"` always kills whatever is there
  first, then creates a fresh one that becomes the persistent shell for subsequent `"session"`
  calls. There is no shell-id parameter on the `Bash` tool schema, and no mechanism for the model
  to address a *specific* one of several shells.
* Reasoning: A multi-shell addressing scheme is a real, separate design problem — a naming/id
  scheme the model would need to track and pass on every call, disambiguation UX when it forgets
  or gets an id wrong, and how that interacts with the standing-interjection reminder (does each
  live shell get its own reminder line? how many before that becomes noise?) — none of which has
  an obvious answer yet, and none of which is needed to make the core feature useful. A single,
  well-known slot already covers the motivating use case (an agent doing a multi-step build/debug
  workflow that needs `cd` and exported-variable state to persist across several tool calls
  in a row) without having to design that scheme first. `shell_lifetime="new"`'s "kill the old one
  and start fresh" semantics are also simpler to specify unambiguously against a single slot than
  against a set of named ones (there's no question of *which* old shell "new" replaces).

  This also sidesteps `TODO.md`'s own follow-on question about parallel tool-call execution:
  `Session._run_tool_calls` dispatches every tool call in a turn's round sequentially today (a
  single `for` loop), so a single persistent-shell slot can never be raced by two calls writing to
  its stdin concurrently. If/when parallel tool-call execution is ever built, both the "how many
  concurrent shells, addressed how" question and this ADR's single-slot answer would need
  revisiting together — deliberately left as future work rather than guessed at now.
