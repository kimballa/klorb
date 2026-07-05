# Move `Workspace` (and workspace-root identity) from `ProcessConfig` onto `SessionConfig`

* Date: 2026-07-05 08:00
* Question: The initial cut of docs/specs/projects-and-trust.md put `workspace: Workspace` on
  `ProcessConfig` — the object shared by every `Session` in a process (see
  [the composition ADR](nest-sessionconfig-inside-a-process-scoped-processconfig.md) for why
  `ProcessConfig` exists at all). That left two separate ideas of "which directory is this"
  floating around at once: `SessionConfig.workspace_root: Path` (the pre-existing hard boundary
  the file tools confine themselves to) and `ProcessConfig.workspace.path` (the same directory,
  wrapped in richer registration/trust metadata) — two fields, on two different objects, that
  must always agree or permission evaluation silently drifts. `ProcessConfig` is explicitly
  documented as holding "settings that must stay identical across every concurrently running
  session" — but `klorb` intends to support multiple concurrent sessions eventually, each
  plausibly pointed at a *different* project directory. Where should workspace identity actually
  live?
* Answer: On `SessionConfig`, as a single `workspace: Workspace` field, replacing
  `workspace_root: Path` outright (not kept alongside it). `ProcessConfig` no longer has any
  workspace-shaped field at all. Every read site that used to reach for
  `SessionConfig.workspace_root` or `ProcessConfig.workspace` now reads
  `SessionConfig.workspace.path` (or `.trusted`) instead — including the permission-evaluation
  code (`klorb.permissions.workspace`), which now branches on
  `context.session_config.workspace.trusted`, not `context.process_config.workspace.trusted`.
  `klorb.process_config.load_process_config(workspace=...)` still accepts and resolves a
  `Workspace` (that resolution — reading `projects.json`, deciding whether the project config
  layer is trusted enough to read — is inherently a startup-time, harness-level concern, not
  per-session data derived from the session itself) but now assembles it into
  `SessionConfig(workspace=...)`, not `ProcessConfig(workspace=...)`.
* Reasoning: `ProcessConfig` is meant for settings that are, by definition, identical across
  every session a process is running — a UI limit, a shared policy table, an endpoint to talk
  to. Workspace identity fails that test on its own stated premise: two sessions in one process
  could legitimately be pointed at two different directories (a workspace picker, a multi-root
  workflow, or simply a future where `/clear`-in-a-different-directory is possible), each with
  its own registration and trust state. Putting `workspace` on `SessionConfig` instead makes it
  exactly as portable as `read_dirs`/`write_dirs` (which already live there for the same
  reason — see `SessionConfig.read_dirs`'s own docstring) and closes the
  `workspace_root`/`workspace.path` duplication for good: there is now exactly one field that
  answers "what directory, and how trusted," per session, not two that happen to usually agree.
  `ProcessConfig.session` remains the *template* a new session's config is copied from (at
  startup, or via `/clear`), so `klorb.tui.repl.ReplApp._apply_workspace_config()` still
  dual-writes the resolved `Workspace` onto both the live session and
  `ProcessConfig.session.workspace` — the same pattern `select_model()`/`set_thinking_enabled()`
  already use — so a future `/clear` in the same directory inherits the already-resolved trust
  decision instead of re-bootstrapping it from scratch.
