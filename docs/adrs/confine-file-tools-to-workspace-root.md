# Confine EditFile/ReplaceAll/CreateFile to a workspace-root placeholder, ahead of real permissions

* Date: 2026-07-01 15:00
* Question: `EditFile`, `ReplaceAll`, and `CreateFile` can all write anywhere on disk the
  process has access to, based solely on whatever `filename` the model supplies. A real
  permission system (allow-lists, interactive confirmation, etc.) is planned but not built
  yet — should these tools ship now with no confinement at all, or with a minimal placeholder?
* Answer: Ship with a minimal placeholder now. `SessionConfig` gains a `workspace_root` field
  (defaulting to the process's cwd via `Path.cwd`), and all three tools resolve their
  `filename` argument through a shared `klorb.tools._path_safety.resolve_within_workspace`
  helper before touching the filesystem, which raises `PermissionError` if the resolved,
  symlink-canonicalized path falls outside `workspace_root`.
* Reasoning: Shipping file-mutating tools with zero path confinement — even temporarily — means
  a model that's tricked, confused, or just wrong about a relative path can write outside the
  project it's supposed to be working on. That risk is easy to close narrowly (one field, one
  shared helper, one check three tools already have to call through) without waiting on the
  fuller permission system's design (allow-lists, interactive prompts, config-file wiring),
  which is real, but larger, follow-up work. `workspace_root` is deliberately not wired into
  `klorb-config.json` yet — it's a runtime default only — since surfacing it as user-facing
  config is part of that later design, not this placeholder.
