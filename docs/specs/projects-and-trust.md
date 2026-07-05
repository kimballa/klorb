# Projects and workspace trust

## Summary

klorb tracks, per directory, whether it's a *registered project* (has a persistent record) and
whether the user *trusts* it — independently of whether that directory happens to contain a
`.klorb/klorb-config.json` file, since a hostile, downloaded-and-unzipped repository could ship
one of those itself. The registry (`projects.json`) lives under `$KLORB_DATA_DIR`, outside any
one workspace, so trust survives across process runs and can't be forged by the workspace it
describes. `SessionConfig.workspace` (a `klorb.workspace.Workspace`) carries the resolved answer
for the owning session; `workspace.trusted` is read directly by the permission-evaluation code
(see [[permissions]] and
[docs/adrs/gate-read-hard-boundary-on-workspace-trust.md](../adrs/gate-read-hard-boundary-on-workspace-trust.md)).
`workspace` lives on `SessionConfig`, not `ProcessConfig`, since multiple concurrent sessions
could each be pointed at a different directory — see
[docs/adrs/move-workspace-from-processconfig-to-sessionconfig.md](../adrs/move-workspace-from-processconfig-to-sessionconfig.md).
Only an untrusted workspace's own `.klorb/klorb-config.json` is ever skipped — every other
config layer (`/etc`, per-user, `--config`) is unaffected.

The interactive REPL additionally *bootstraps* an unregistered workspace the first time it's
opened (asking whether to make it a project, and whether to trust it) and offers a "Trust
workspace" command for one left untrusted. Both are TUI-only: a headless one-shot prompt can't
ask anything, so it only ever sees whatever trust decision a previous interactive session
already recorded.

## How it works

### `Workspace` (`klorb.workspace`)

A `Workspace` (pydantic model) is `{id: str | None, path: Path, is_project: bool, trusted:
bool}`. `id` is the project's key into `projects.json`, or `None` if it has no persistent record
yet; `is_project` is `True` exactly when `id` is not `None`. It's never loaded from
`klorb-config.json` — no on-disk key exists for it at all, the same reason
`SessionConfig.workspace` itself has none (see [[process-and-session-config]]'s "Configuration"
section). There is deliberately no separate `is_workspace_trusted` bool mirroring
`workspace.trusted` on either config object — an earlier version of this feature had one, kept
manually in sync by every place that changed either, until it was removed as a needless
duplication risk; see docs/adrs/consolidate-workspace-trust-into-a-single-field.md.

### `projects.json` (`klorb.workspace.trust_manager`)

`TrustManager` is the single owner of all reads and writes to `$KLORB_DATA_DIR/projects.json`
(`projects_path()`) — schema-enveloped (see [[persisted-json-schema-versioning]]) as
`{"schema": {"name": "klorb-projects", "version": "1.0.0"}, "projects": [...]}`, where each
entry is a `ProjectRecord`: `{id: str, path: str, trusted: bool}` (`path` canonicalized via
`Path.resolve()`). It's constructed once per process (`klorb.cli.main()`) and passed explicitly
to every collaborator that needs it, rather than a module-level global — see
[docs/adrs/thread-trustmanager-explicitly-not-a-global-singleton.md](../adrs/thread-trustmanager-explicitly-not-a-global-singleton.md).

`TrustManager.resolve_workspace(cwd)` is the deterministic half of resolution — it never prompts
anyone and never writes anything:

1. If `cwd` itself (canonicalized) has a `projects.json` entry, that's the workspace, returned
   trusted/untrusted exactly as recorded.
2. Otherwise, if any ancestor of `cwd` has an entry, that ancestor is the workspace root (a
   subdirectory of a known project is still that project).
3. Otherwise, falls back to
   `klorb.permissions.directory_access.find_workspace_root()`'s own ancestor search for a
   `.klorb/` directory (which may exist without ever having been registered — e.g. a
   downloaded-and-unzipped repository that ships its own `.klorb/klorb-config.json` — falling
   back to `cwd` itself if none is found), returned as an unregistered (`id=None`,
   `is_project=False`), untrusted `Workspace`.

`TrustManager.register_project(path, trusted)` appends a new `ProjectRecord` with a fresh
`uuid4` id and persists it; `TrustManager.set_trusted(project_id, trusted)` updates and persists
an existing record's flag (raising `KeyError` if the id isn't found).

### `SessionConfig.workspace` and trust-gated config loading (`klorb.process_config`)

`load_process_config(*, config_flag_path=None, cwd=None, workspace=None)` takes a `workspace:
Workspace | None` parameter. When omitted, a conservative one is synthesized:
`Workspace(path=find_workspace_root(cwd))` (unregistered, untrusted) — the same ancestor search
`SessionConfig.workspace` has always used, so every pre-existing caller (tests
constructing a `ProcessConfig` directly, or `load_process_config()` called with no `workspace`
argument) is unaffected. The per-project config layer
(`workspace.path/.klorb/klorb-config.json`) is read only when `workspace.trusted` is `True`;
when it isn't, that layer is skipped entirely — not read and then discarded, *never opened* —
so an untrusted project's `readDirs.allow` can't even reach the "unrecognized key" warning path,
let alone the concatenated `readDirs`/`writeDirs` lists. The returned `ProcessConfig.session.workspace`
is this same `Workspace` (never routed through `SESSION_KEY_MAP`/`PROCESS_KEY_MAP`, neither of
which has an entry for it) — this is the one production code path allowed to set
`workspace.trusted = True`, gated entirely on the harness-resolved `Workspace`, never on
anything a config file said. `workspace` lives on `SessionConfig`, not `ProcessConfig` — see
[docs/adrs/move-workspace-from-processconfig-to-sessionconfig.md](../adrs/move-workspace-from-processconfig-to-sessionconfig.md).

`klorb.cli.main()` constructs one `TrustManager`, calls `resolve_workspace(Path.cwd())`, and
passes the result into `load_process_config(...)` — for *both* the headless one-shot path and
the interactive REPL, so a headless run against a previously-trusted project still gets that
project's config layer and expanded `ReadFile` boundary (see
[[permissions]]'s "read/trust" ADR).

### Writing a project's config file (`klorb.workspace.workspace_init`)

* `write_initial_project_config(workspace_root, model_name, trusted)` writes a starter
  `${workspace_root}/.klorb/klorb-config.json`: `readDirs.allow` always includes
  `workspace_root`; `writeDirs.allow` includes it too only if `trusted` (an untrusted-but-opened
  project's `writeDirs` stays empty, so writes fall through to the interactive "ask" flow rather
  than defaulting to allowed); `sessionDefaults.model` is burned in as `model_name` (the
  session's currently-active model at bootstrap time).
* `write_session_defaults_to_project_config(workspace_root, config)` dumps a live
  `SessionConfig`'s current scalar fields (model, thinking settings, tool-call limits) and
  `readDirs`/`writeDirs` into the same file — used when a previously-unregistered project is
  trusted later, so any `ask`-derived grants built up before that decision aren't lost.
* Both write directly via `klorb.schema_envelope.write_versioned_json`, like
  `klorb.permissions.grant` — trusted harness code reacting to an explicit interactive decision,
  not the agent, so neither is subject to (or needs to route through) the `.klorb/`
  self-tampering protection in `klorb.permissions.directory_access.privileged_dirs()`.
* Deliberately *not* re-exported from the `klorb.workspace` package's `__init__.py` (unlike
  `TrustManager`): `workspace_init` imports `klorb.process_config` for its schema/key constants,
  and `klorb.process_config` imports `Workspace` from `klorb.workspace` — re-exporting
  `workspace_init` there too would make that a real import cycle. Callers (`klorb.tui.repl`)
  import it from `klorb.workspace.workspace_init` directly.

### Interactive bootstrap (`klorb.tui.repl.ReplApp`)

Entirely opt-in: `ReplApp.__init__(trust_manager: TrustManager | None = None)` defaults to
`None`, under which every piece of this section is a no-op — see
[docs/adrs/gate-workspace-bootstrap-on-an-explicit-trust-manager.md](../adrs/gate-workspace-bootstrap-on-an-explicit-trust-manager.md).
Only `klorb.cli.main()`'s real invocation passes a `TrustManager`.

`on_mount()` (synchronous) hands off to `_run_startup_workspace_and_initial_message()`, a
`@work()`-decorated method (a Textual worker, not a thread), which awaits
`_resolve_workspace_trust()` before submitting any `initial_message` — so the first turn never
races the bootstrap. `@work()` is required here (and on `trust_workspace()`, below) because both
push a `ConfirmScreen` and await its dismissal (`push_screen_wait`), which Textual only permits
from an active worker context — see
[docs/adrs/run-modal-driven-workspace-flows-as-textual-workers.md](../adrs/run-modal-driven-workspace-flows-as-textual-workers.md).

`_resolve_workspace_trust()`:

* If `trust_manager` is `None`, returns immediately.
* If `SessionConfig.workspace.id` is not `None` (already registered — `load_process_config()`
  resolved it via an exact or ancestor `projects.json` match), skips straight to announcing it.
* Otherwise (`id is None` — never resolved before), runs `_bootstrap_new_workspace()`: pushes
  "You are working in `<path>`. Open as a project?" (Yes: "Open as project", No: "Not now"),
  then always pushes "Do you trust the workspace at `<path>`?", regardless of the first answer.
  If opened as a project, registers it (`TrustManager.register_project`) and writes its starter
  config (`write_initial_project_config`, burning in the *current* session's model) using the
  trust answer; otherwise returns an unregistered `Workspace` carrying only the trust decision,
  which lives in memory for the rest of this session's lifetime (never persisted, since there's
  no project record to persist it to).
* Either way, `_apply_workspace_config()` reconciles the live config against the (possibly new)
  `Workspace` (see below), then `_announce_workspace()` mounts one history notice: `"Working in
  project: <path>"` if trusted, or `"The workspace at <path> is not trusted. Run `>Trust
  workspace` to change this."` otherwise.

`_apply_workspace_config(workspace)` re-runs `load_process_config(config_flag_path=...,
cwd=workspace.path, workspace=workspace)` and applies the result *in place* — mutating the
existing `ProcessConfig`/`SessionConfig` objects rather than reconstructing `Session`/
`ToolRegistry` (both hold references to these same objects, not copies, so every tool sees the
change on its very next call with nothing to rebuild). `workspace` itself is dual-written onto
both the live `Session.config` and the `ProcessConfig.session` template — the same pattern
`select_model()`/`set_thinking_enabled()` already use for session-scoped settings — so a future
`/clear` in this process inherits the resolved trust state rather than re-bootstrapping it.
`read_dirs`/`write_dirs` are *concatenated* onto the live session's own (never replaced), so an
"Allow (this session)" grant made earlier isn't discarded by the reload; every other
process-only field is overwritten outright; `session`'s other scalar fields (model, thinking,
tool-call limits) are deliberately left alone, since a config file's declared defaults shouldn't
silently override a value the user may have already picked interactively earlier in the same
session.

### The "Trust workspace" command (`klorb.tui.trust_commands`, `ReplApp.trust_workspace`)

`TrustWorkspaceCommandProvider` offers "Trust workspace" via the palette (`Ctrl+P`, or
`>trust`-narrowed from the prompt) exactly while `workspace_trust_management_enabled()` (a
`TrustManager` was given) and `not is_workspace_trusted()` — a trusted, or trust-management-
disabled, app yields no hits, so the command simply doesn't show up.

`ReplApp.trust_workspace()` (`@work()`, for the same `push_screen_wait` reason as above):
confirms "Do you trust the workspace at `<path>`?"; on Yes, persists the decision
(`TrustManager.set_trusted`, if it's a registered project) and calls `_apply_workspace_config()`,
then mounts `"Trusted workspace <path>."`. If the workspace is a registered project with no
`.klorb/klorb-config.json` of its own yet, additionally asks "Initialize the project config file
with your current session settings?" — on Yes, `write_session_defaults_to_project_config()`
captures the live session's current `readDirs`/`writeDirs` (and other settings) into that file,
so they aren't lost the next time klorb opens this workspace.

## Configuration

* `$KLORB_DATA_DIR/projects.json` — see "`projects.json`" above. Not user-hand-authored in
  practice (though nothing stops it); written and read exclusively through `TrustManager`.
* No new `klorb-config.json` keys. `Workspace` (and `workspace.trusted`) remains unsettable from
  any config file, by design (see [[process-and-session-config]]).

## Out of scope

* A headless one-shot run never bootstraps or prompts anything — it only ever sees whatever
  `projects.json` already records for the resolved workspace (or its ancestors). There is no
  non-interactive way to register or trust a new project (e.g. no `klorb init --project` /
  `--trust` flag); that would need its own follow-up.
* `EscalatePrivileges` (a temporary, user-confirmed exception to `${workspace_root}/.klorb/`'s
  own unconditional read/write deny) is unrelated to, and unaffected by, this feature — see
  `TODO.md`'s "Permissions" item and [[permissions]].
* Project-level system-prompt overrides don't exist yet (`klorb.system_prompts.resolve_prompt_file`
  only checks the per-user tier and the packaged default) — so this feature's requirement that
  "system prompts must not load from an untrusted project" is currently vacuous, not implemented
  against a real mechanism. Whenever such a per-project tier is built, it must be gated on
  `SessionConfig.workspace.trusted` the same way the config-file layer is here.
* `last-session.json` (`TODO.md`) doesn't exist yet, so there's no "reconstitute the previous
  session automatically" behavior tied to a trusted workspace — a possible future extension, not
  built here.
* The top status bar does not yet show the current workspace name or an `(Untrusted)` marker —
  see `TODO.md`'s "Feature backlog".
