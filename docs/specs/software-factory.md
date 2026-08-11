# Software Factory

## Summary

Software factory is an inward-facing configuration of this repo's own `.klorb/` directory that
lets a klorb session working on this repo pull small tasks from `docs/plans/auto/` one at a
time, build each on its own branch, get it reviewed, open a PR, and restart itself for the next
task — unattended. It is not a klorb platform feature; it's built entirely out of the hooks,
events, and skills mechanisms `docs/specs/hooks-and-events.md` and `docs/specs/skills.md`
already implement and document. This spec covers only what's specific to this configuration:
the sentinel files, the queue file format, the four hook/event wirings, and the loop-termination
logic.

## Sentinel files

Both live under `docs/plans/auto/`, are gitignored, and are plain files with no content that
matters — only their presence/absence is read.

* **`.enable_software_factory.tmp`** — the on/off switch. Created by the `onActivateSkill` hook
  (`.klorb/hooks/software-factory/enable_sentinel.sh`) when `/enable-software-factory` is
  activated. Removed unconditionally by the `onProcessEnd` hook
  (`.klorb/hooks/software-factory/disable_sentinel.sh`), so the mode never survives past the
  klorb process that enabled it.
* **`.factory_in_progress.tmp`** — set by the `enable-software-factory` skill itself once it
  picks a task and passes its own clean-tree precondition (before branching), cleared by the
  skill once a task's full lifecycle finishes. Since it's gitignored it never appears in
  `git status --porcelain`, which is what lets `on_turn_end.py` tell "the factory's own task is
  mid-flight" apart from "the tree is clean" or "something unrelated is dirty."

Both filenames, and the `docs/plans/auto` path itself, are defined once in
`.klorb/hooks/software-factory/queue_utils.py` (`ENABLE_SENTINEL_NAME`,
`IN_PROGRESS_SENTINEL_NAME`, `AUTO_DIR_RELATIVE`) and imported by both Python hook scripts. The
two bash scripts hardcode the same literal filenames, since bash can't import a Python constant.

## Queue file format

`docs/plans/auto/queue.md` and any sibling `.md`/`.txt` file are task sources. A **task line** is
a line with no leading whitespace whose first characters are `-` or `*` — top-level bullets
only; a nested sub-bullet is detail belonging to its parent task, not a separate task. Every
other line (blank, heading, prose, HTML comment) is not a task, so `queue.md` can carry an
explanatory header with no false positive. "Is there pending work?"
(`queue_utils.has_pending_work()`) is: any task line in `queue.md`, OR any other `.md`/`.txt`
file directly under `docs/plans/auto/` besides `queue.md` (each such file is a single,
self-contained, whole-file task).

## Hook and event wiring

Configured in `.klorb/klorb-config.json`; see `docs/specs/hooks-and-events.md` for the general
hook/event mechanics these entries rely on.

| Hook/event | Handler | Behavior |
| --- | --- | --- |
| `onActivateSkill`, filtered to `skill_name matches "enable-software-factory"` | `enable_sentinel.sh` (bash) | Creates `docs/plans/auto/` if needed, touches the enable sentinel. |
| `onProcessEnd` | `disable_sentinel.sh` (bash) | Removes both sentinel files. `rm -f`, so a missing file never produces a nonzero exit. |
| `onAgentTurnEnd` | `on_turn_end.py` (python3, stdlib only) | See "Loop termination" below. |
| `FileSystemModified`, `watch: "docs/plans/auto"` | `on_file_changed.py` (python3, stdlib only) | Nudges the session when new work appears while the factory is idle. |

The two Python handlers are invoked as `["python3", "<script path>"]` rather than through a
shell, so `${workspaceRoot}` macro-expansion applies to the script path itself.

## Loop termination

`on_turn_end.py` runs after every agent turn in every klorb session against this workspace, on
or off, but no-ops immediately whenever the enable sentinel is absent. When present:

1. **Dirty tree, `.factory_in_progress.tmp` present** — the factory's own task is mid-flight but
   the turn ended before finishing. Emits a plain continuation message (no `reset_session`,
   since wiping the conversation would lose what it was mid-way through). This relies on
   `tools.hooks.maxChainedTurns` (default 5, `docs/specs/hooks-and-events.md`) as the backstop:
   once that many consecutive auto-continued turns have run without a real user/tool-driven turn
   resetting the counter, the chained-turn cap refuses to queue another one, and the task
   simply stops making unattended progress until a human looks at it.
2. **Dirty tree, `.factory_in_progress.tmp` absent** — dirt that isn't the factory's; no
   message. (The `enable-software-factory` skill's own clean-tree precondition, step 1, is what
   keeps stray human work from ever becoming "the factory's" in the first place.)
3. **Clean tree, pending work** (`queue_utils.has_pending_work()`) — emits `reset_session: true`
   with a message telling the agent to run `/enable-software-factory` for the next task. This is
   the "restart for the next task" step; each such restart also counts against
   `maxChainedTurns`.
4. **Clean tree, no pending work** — no message. The loop stops itself; the session sits idle
   until a human adds more work or talks to it directly.

`on_file_changed.py` exists to shorten the gap between a human adding work and the factory
noticing, for the case where the factory is already idle (sentinel present, no task in
progress, agent not mid-turn — `is_agent_active` from `EventInput`). It filters out `fs_updates`
entries for the sentinel files themselves (the watcher sees their own create/delete events too),
then nudges only if `queue.md` changed and now has a task line, or a new `.md`/`.txt` task file
was created.

## `main`-branch cleanup stays local

See `docs/adrs/00185-software-factory-main-cleanup-commits-stay-local.md`. After a task's PR is
opened from its feature branch, the `enable-software-factory` skill also removes the same task
from `main` directly (same queue-bullet or whole-file removal) and commits it there — but never
pushes that commit. This is what keeps the next iteration, which always starts by reading
`main`, from re-picking the same task while the PR is still pending review, without turning
`main`-pushes into something a hook or skill decides unattended.

## Unattended git/gh commands

See `docs/adrs/00184-widen-commandrules-allow-for-unattended-git-and-gh.md`. Running this loop
with no human present to answer a permission prompt requires `git checkout`/`branch`/`add`/
`commit`/`push`/`rm`/`mv` and `gh pr create` to already be allow-listed in this workspace's
`commandRules`, rather than landing on `ask`.
