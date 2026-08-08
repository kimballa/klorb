# Nest SessionConfig inside a process-scoped ProcessConfig, rather than one shared model

* Date: 2026-07-01 09:00
* Question: klorb will eventually run multiple `Session`s concurrently in one process, and
  already loads settings from config files, `--config`, and (later) saved last-session
  state on top of CLI flags. Where should settings that must be identical across every
  session in the process (e.g. a UI limit, or a permission a session can't override) live,
  relative to `SessionConfig`? Should `ProcessConfig` and `SessionConfig` be the same
  pydantic model, should `ProcessConfig` be a strict superset of `SessionConfig`'s fields
  sliced down at session-creation time, or should `ProcessConfig` nest a `session:
  SessionConfig` field?
* Answer: Nest a `session: SessionConfig` field inside `ProcessConfig`
  (`klorb/src/klorb/process_config.py`). `ProcessConfig.session` is a template: a fresh
  `Session` (at startup, or via `/clear`) gets its own `SessionConfig.model_copy()` of it, so
  concurrent sessions never share one mutable `SessionConfig` instance. Process-only settings
  are sibling fields on `ProcessConfig` itself, read directly (not copied), so there's only
  ever one value in memory to keep in sync. A small set of session-editable settings (today:
  model, thinking enabled, thinking effort) get set on both the live session's `SessionConfig`
  and `ProcessConfig.session` in the same call, so future sessions in the process inherit the
  change without extra plumbing.
* Reasoning: A single shared model (`ProcessConfig == SessionConfig`) conflates two
  different lifetimes — a process-only field like a UI limit would need to either live on
  every session (requiring a fan-out update to every live session when it changes) or be
  bolted on awkwardly, and "a permission the session can't override" becomes unrepresentable
  since there's only one object either level could mutate. A superset model with
  field-slicing avoids the nesting syntax, but the session/process boundary becomes an
  implicit convention (which fields count as "session fields" when slicing?) that a future
  field addition could silently violate. Nesting makes the boundary a schema fact instead:
  anything under `.session` is per-session and clonable, anything sibling to it is a process
  singleton by construction. The one cost — `process_config.session.model` instead of a flat
  `process_config.model` from inside `Session`-adjacent code — is a fair trade for that
  guarantee as concurrent sessions become real.
