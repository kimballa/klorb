# Read workspace trust off `ProcessConfig.workspace.trusted` alone; remove `is_workspace_trusted`

* Date: 2026-07-05 07:00
* Question: The initial cut of docs/specs/projects-and-trust.md kept both
  `ProcessConfig.is_workspace_trusted: bool` (the original field from
  [the read/trust ADR](gate-read-hard-boundary-on-workspace-trust.md), which every permission-
  evaluation call site already read) and the new `ProcessConfig.workspace: Workspace` (whose own
  `trusted` field is the "real," harness-resolved answer), manually kept in sync by every place
  that changed either one (`load_process_config`, `ReplApp._apply_workspace_config`,
  `ReplApp.trust_workspace`). Two fields recording the same fact, synchronized by convention
  rather than by construction, is exactly the kind of drift hazard that's easy to introduce and
  hard to notice: a future call site that sets one and forgets the other would silently desync
  trust state from the permission checks that gate `ReadFile`'s workspace-root boundary — a
  security-relevant bug, not a cosmetic one. Should the dual-field design stay, or should one of
  the two be removed?
* Answer: Remove `ProcessConfig.is_workspace_trusted` entirely. Every reader
  (`klorb.permissions.workspace.resolve_and_evaluate_read`, `ReplApp.is_workspace_trusted()`,
  test helpers) now reads `ProcessConfig.workspace.trusted` directly. `load_process_config()` no
  longer sets a second field from `workspace.trusted`; `ReplApp._apply_workspace_config()`/
  `trust_workspace()` only ever assign `self._process_config.workspace = <the new Workspace>`,
  with nothing else to keep in step.
* Reasoning: `workspace.trusted` is the actual source of truth — it's what `TrustManager`
  resolves, persists to `projects.json`, and hands back — so `is_workspace_trusted` was never
  independent data, only a cached copy one call site had to remember to refresh every time
  `workspace` changed. A cache with exactly one legitimate writer and no invalidation mechanism
  beyond "don't forget" isn't earning its keep; every one of its three write sites already had
  the real `Workspace` in hand, so reading `.trusted` off it directly costs nothing. Removing
  the field also shrinks `ProcessConfig`'s surface (one less thing a new field/test has to
  remember exists) and makes "which one is authoritative" no longer a question a future reader
  has to resolve by re-deriving it from the docstrings. `ReplApp.is_workspace_trusted()`/
  `SupportsTrustWorkspace.is_workspace_trusted()` (the query *methods*, used by
  `TrustWorkspaceCommandProvider` to decide whether the palette command should be visible) are
  unrelated to this and are kept as-is — only the redundant `ProcessConfig` bool field is
  removed, not the method that happens to share its name.
