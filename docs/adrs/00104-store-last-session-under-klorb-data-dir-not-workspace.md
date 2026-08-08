# Store `last-session.json` under `$KLORB_DATA_DIR`, not inside the workspace

* Date: 2026-07-12 00:00
* Question: `TODO.md`'s original note for saved-session-state sketched
  `${workspace_root}/.klorb/last-session.json` — right alongside `klorb-config.json`, inside the
  project itself. But that file would carry the full message history of a conversation:
  file contents `ReadFile` returned, shell output, tool arguments, potentially secrets a prior
  turn handled. Where should it actually live?
* Answer: `$KLORB_DATA_DIR/projects/<token>-<basename>/last-session.json` — the same
  per-project directory `klorb.workspace.input_history` already uses for the prompt-recall
  file, outside any workspace, keyed by project identity (`Workspace.id`, or a stable hash of
  the canonical path for an unregistered-but-trusted workspace) rather than by the workspace's
  own path.
* Reasoning: Everything else that already answers "does this directory get to see harness
  state" answers it the same way — `projects.json` (trust records), the input-history store,
  and now this, all live under `$KLORB_DATA_DIR`, never inside the workspace itself. Putting
  conversation history inside `${workspace_root}/.klorb/` would mean:
  * A hostile, downloaded-and-unzipped repository could ship a fake `last-session.json` of its
    own — the same supply-chain concern `docs/specs/projects-and-trust.md` already documents
    for `.klorb/klorb-config.json` ("a hostile, downloaded-and-unzipped repository could ship
    one of those itself"). A forged save file that gets auto-loaded and replayed as prior
    conversation history is a strictly worse version of that same attack: it could plant fake
    tool results or assistant turns the next session would treat as its own genuine history.
  * A workspace's own `.gitignore` would have to remember to exclude a file that may contain
    file contents, shell output, or other turn-scoped data the user never intended to commit —
    an easy thing to forget, and a worse blast radius than a stray `klorb-config.json` tweak.
  * It's the one save slot that already keys correctly on project *identity*, not path: an
    unregistered-but-trusted workspace still gets a stable location via the existing
    path-hash fallback in `klorb.workspace.input_history.project_history_dir` — no separate
    mechanism to build or keep in sync.
  Reusing `project_history_dir` (rather than introducing a second, near-identical per-project
  directory helper) also avoids duplicating the token/hash logic that decides where a
  project's files live — the risk `CLAUDE.md`'s "never duplicate a constant" rule calls out.
