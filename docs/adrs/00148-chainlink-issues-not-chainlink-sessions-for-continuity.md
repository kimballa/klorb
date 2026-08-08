# Chainlink task tracking uses Issues (comments + dependency graph), not chainlink's own Session/handoff feature

* Date: 2026-07-20 00:00
* Question: Chainlink ships its own Session concept (active work assignments, handoff notes,
  breadcrumb actions, idle detection) purpose-built for AI-agent context continuity. Klorb also
  has its own `Session` (docs/specs/session-and-turns.md), with its own turn loop, standing
  interjections, and its own persistence/restore path (docs/specs/session-persistence.md). Should
  the chainlink task-tracking integration use chainlink's Session/handoff-note/breadcrumb
  machinery to carry state across context compaction or a handoff between klorb sessions, or rely
  only on chainlink's Issue-level data?
* Answer: Use only chainlink Issues -- specifically their comments and their dependency
  ("blocks"/"blocked by") graph -- as the continuity mechanism. Chainlink's own Session,
  handoff-note, breadcrumb, and idle-detection features are not used at all. Klorb's own `Session`
  remains the single source of truth for what "a session" is; the tracked
  `Session.cur_chainlink_task_id` field, together with comments and dependencies recorded on
  issues by the `TASKS`-category tools, is how task-relevant state survives context compaction or
  a handoff between klorb sessions.
* Reasoning: Two independent "session" concepts covering the same conceptual ground would compete
  rather than complement each other. Chainlink's session start/work/action/end and handoff notes
  assume it owns tracking what an agent is currently doing, but klorb already owns that via its
  own `Session`, turn loop, and standing interjections, and already has its own persistence and
  restore path. Layering chainlink's session state on top would produce two separate,
  unsynchronized records of "what's currently being worked on" that could drift from each other,
  and would double the surface area a later "resume a prior session's leftover work" feature
  would need to reconcile. Comments and the dependency web are a narrower, additive slice of
  chainlink: they attach to Issues, which the `TASKS` tools already read and write for their own
  purposes (`TodoList`/`TodoCreate`/`TodoUpdate`), so no second state machine is introduced.
  Multiple klorb sessions sharing one `.chainlink/issues.db` (e.g. across worktrees) is a
  consistency concern -- not wanting concurrent sessions to clobber each other's issues -- rather
  than a security boundary; label-based scoping is not access control, and enforcing isolation
  between concurrent sessions in that scenario is future work.
