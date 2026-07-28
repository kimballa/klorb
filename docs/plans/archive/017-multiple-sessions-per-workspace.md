# Plan 17: Multiple sessions per workspace

## Status

Ready.

## Summary

Today a workspace remembers exactly one saved session: `klorb.workspace.last_session` writes a
single `last-session.json` per project directory, and reopening klorb in that workspace
restores it unconditionally. This plan replaces that single-slot store with a small, per-project
database of recent sessions — an ordered `sessions.json` index plus one `session.json` per
session, living in its own subdirectory — so a workspace can hold onto many past conversations,
not just the last one, and a user (TUI or VS Code) can pick which one to resume.

This plan touches every layer that owns session lifecycle: the on-disk format
(`klorb.workspace`), `Session` itself (which becomes the owner of its own persistence and lock,
not something external code writes on its behalf), the TUI (a new "Load session" picker, and
simplified always-save-on-quit behavior), the ACP server (real `session/list`/`session/load`-style
support where today those are stubbed `method_not_found`), and the VS Code extension (new-session
and session-history icons, a resume-latest-session setting, and a history-replay protocol).

It also introduces klorb's first file-locking primitive (`klorb.lockfile`), since — unlike
`projects.json`'s update-in-place-per-record pattern or `history`'s append-only design — this
feature needs to know when a session subdirectory is actively owned by a live process.

## Relationship to existing specs

* Supersedes `docs/specs/session-persistence.md` in full — that document describes the
  single-slot `last-session.json` design this plan replaces. Once this plan is implemented, that
  spec is rewritten (not just amended) to describe the new multi-session design; the "Out of
  scope" bullet in that spec noting "no history of saved sessions" is exactly what this plan
  removes.
* Builds on `docs/specs/projects-and-trust.md` (`klorb.workspace.trust_manager.TrustManager`,
  `projects.json`) and `klorb.workspace.input_history.project_history_dir` (the per-project
  directory under `$KLORB_DATA_DIR/projects/<token>-<basename>/` this plan's `sessions/` subtree
  lives inside).
* Builds on `docs/specs/persisted-json-schema-versioning.md` (`klorb.schema_envelope`) for every
  new on-disk JSON file.
* Extends `docs/specs/klorb-server.md`'s "Protocol surface not implemented at this checkpoint"
  section — `session/load`/`session/list` move from `method_not_found` stubs to (partial) real
  implementations; `session/new`'s "owns at most one `Session` at a time... replace, don't add"
  behavior is unchanged (a klorb process/TUI/ACP connection is still single-session at a time —
  what's new is that *several* single sessions, over time, are all individually resumable).

## Out of scope

* No migration of any existing `last-session.json` file — the user will remove stale files by
  hand. `klorb.workspace.last_session` (module, schema name `"klorb-session"`) is retired
  outright, not read as a fallback.
* Subagent sessions (not yet implemented at all) do not get their own top-level session
  directory under this plan — a future subagent's state is tracked somewhere inside its root
  session's own directory, exact mechanism deferred to whatever plan implements subagents.
* No cross-machine sync, export, or search of past sessions — this is purely a per-workspace,
  per-machine on-disk store.
* Windows lock support — `klorb.lockfile`'s factory raises `NotImplementedError` on a platform
  that's neither POSIX-fcntl-capable Linux/WSL nor macOS, matching how `klorb.tui.shell` and
  other POSIX-only subsystems already treat Windows as unsupported.

## Data model & file layout

All paths below are relative to `klorb.workspace.input_history.project_history_dir(workspace)` —
the same per-project directory `history` and (formerly) `last-session.json` already live in:

```text
$KLORB_DATA_DIR/projects/<token>-<basename>/
├── history                       # unchanged: append-only prompt-recall log
├── workspace.lock                # guards writes to sessions.json (see "Locking" below)
└── sessions/
    ├── sessions.json             # ordered index of recent sessions (schema klorb-session-list)
    └── <subdir>/
        ├── session.json          # one saved session's full state (schema klorb-session)
        └── session.lock          # held by the live process that owns this session, if any
```

`sessions/` and `sessions/<subdir>/` are created lazily, the same way `project_history_dir`
itself is today — nothing under `projects/<token>-<basename>/` is pre-created at workspace
resolution time.

### `sessions.json`

New module `klorb.workspace.session_store` (see "Module layout" below) owns this file.

```python
class RecentSession(BaseModel):
    session_id: str
    subdir: str
    title: str | None = None

class SessionsIndexState(BaseModel):
    recent_sessions: list[RecentSession] = Field(default_factory=list)
```

* `session_id` is the session's `Session.id` (which may be renamed in place by the session-naming
  classifier — see `klorb.session_naming.rename_session_id` — so this field can change value for
  an existing `RecentSession` entry without the entry moving or being duplicated).
* `subdir` is the directory name under `sessions/` this session's files live in. Initialized to
  the session's *original* `Session.id` at the moment its directory is first created, and never
  renamed afterward — this is deliberately decoupled from `session_id` so a future change could
  relocate or rename a session's id without needing to move its files, or vice versa.
* `title` mirrors `Session.name` (the classifier-assigned or fallback-derived title — see
  "Session title fallback" below); `None` only in the narrow window before a title has been
  assigned at all (should not normally be observed on disk, since a session's directory isn't
  created until after that decision is made — see "When a session claims its directory" below).
* Schema: `name="klorb-session-list"`, `version="1.0.0"`.
* JSON field names are the plain (snake_case) pydantic field names, via `model_dump(mode="json")`
  — this deliberately matches the existing convention for every other *machine-written* persisted
  file in the codebase (`LastSessionState`'s `session_id`/`root_id`/`cur_chainlink_task_id`,
  `ProjectRecord`'s fields), not the dot-delineated `lowerCamelCase` convention
  `docs/specs/process-and-session-config.md` documents for *hand-authored* config keys
  (`klorb-config.json`). `sessions.json`/`session.json` are never hand-edited, so there's no
  reason to special-case them against the sibling files they sit next to on disk.
* Ordering: index 0 is the most recently touched session (see "Recency updates" below); no other
  field records a timestamp — order in the array *is* the recency signal, the same way
  `input_history`'s append-only file uses position instead of a timestamp field.

### `session.json`

Reuses the existing `LastSessionState` shape (`klorb.workspace.last_session`, schema
`"klorb-session"` v`"1.0.0"`) verbatim — `config`, `messages`, `statistics`, `session_id`,
`root_id`, `session_name`, `cur_chainlink_task_id` — just relocated from
`.../last-session.json` to `.../sessions/<subdir>/session.json`. `klorb.workspace.last_session`
is renamed/folded into `klorb.workspace.session_store` (see "Module layout"); no schema or field
change, so an *existing* `last-session.json` (if a user copied it into a session's directory by
hand) would still validate — though this plan doesn't do anything to encourage or automate that.

### `MAX_RECENT_SESSIONS` and pruning

`klorb.workspace.session_store.MAX_RECENT_SESSIONS = 30`. Whenever `sessions.json` is rewritten
with more than `MAX_RECENT_SESSIONS` entries, entries beyond the limit (from the tail — i.e. the
least recently touched) are dropped from the index *and* their `sessions/<subdir>/` directory is
deleted (`shutil.rmtree`), **except** any entry whose directory currently holds a live
`session.lock` (see "Locking" below) — a locked session is skipped over (kept in the list, not
counted against the cap for this pass), which is why the list can legitimately grow past
`MAX_RECENT_SESSIONS` when many sessions are simultaneously live (e.g. several klorb processes
open against the same workspace).

## Locking (`klorb.lockfile`)

New top-level package, `klorb/src/klorb/lockfile/`. Nothing in the codebase does file locking
today (`grep`-confirmed during this plan's research) — `TrustManager`'s per-process-instance
discipline and `input_history`'s append-only design each sidestep the need in their own way, but
"is another process currently occupying this session?" has no answer without a real lock.

```python
# klorb/lockfile/__init__.py
class Lockfile(Protocol):
    """A non-blocking, whole-file exclusive lock on a path, held for the lifetime of the
    process that acquires it (dropped automatically if the process dies, since the OS releases
    the underlying file descriptor's lock on close/exit either way — but every long-lived
    holder must still release explicitly on a clean shutdown, so a "did I release?" question
    never depends on process death to answer it)."""

    @property
    def path(self) -> Path: ...

    def try_acquire(self) -> bool:
        """Attempt to acquire the lock without blocking. Returns `True` on success (idempotent
        if already held by this instance), `False` if another process holds it."""

    def release(self) -> None:
        """Release the lock if held; a no-op otherwise."""

    def is_held_by_other(self) -> bool:
        """Probe whether some *other* process currently holds this lock, without attempting to
        acquire it for this process — used by the pruning pass and by "is this session
        occupied?" checks that must not perturb a lock this process doesn't already hold."""


def create_lockfile(path: Path) -> Lockfile:
    """Factory: returns the concrete `Lockfile` implementation for the running platform —
    `klorb.lockfile._linux.OfdLockfile` on Linux/WSL (`sys.platform == "linux"`), `klorb.
    lockfile._macos.PosixLockfile` on macOS (`sys.platform == "darwin"`). Raises
    `NotImplementedError` on any other platform (Windows) — see this plan's "Out of scope"."""
```

* **Linux/WSL** (`klorb/lockfile/_linux.py`): non-blocking Open File Description lock via
  `fcntl.fcntl(fd, F_OFD_SETLK, ...)` — the pattern given in this plan's design notes. `F_OFD_
  SETLK`/`F_RDLCK`/`F_WRLCK` (37/0/1) aren't exposed by Python's `fcntl` module, so they're
  defined as local constants, matching the reference sketch. `is_held_by_other()` uses the
  read-only counterpart `F_OFD_GETLK` (36) to query without acquiring.
* **macOS** (`klorb/lockfile/_macos.py`): classic (non-OFD) advisory record lock via
  `fcntl.fcntl(fd, fcntl.F_SETLK, ...)` with the same `struct.pack('hhqqi', ...)` layout — OFD
  locks aren't available on macOS. This is process-associated rather than descriptor-associated
  (the platform gap this plan's design notes call out), which is an accepted limitation: a
  single klorb process only ever holds one `session.lock` at a time in practice (its one live
  `Session`), so the two locking semantics are indistinguishable for this feature's actual usage
  pattern.
* Both implementations open the file with `os.open(path, os.O_CREAT | os.O_RDWR, 0o666)`,
  matching the reference sketch, and never delete the lock file itself on release — only the
  *lock* is released; a stale zero-byte `.lock` file left on disk is harmless and is reused (not
  recreated) by the next acquirer.

### Retry-with-backoff helper

```python
def acquire_lockfile_with_backoff(
    path: Path, *, max_attempts: int = 4, initial_delay_seconds: float = 0.1,
) -> Lockfile | None:
    """Create a lockfile at `path` and try to acquire it, retrying on contention up to
    `max_attempts` times total with exponential backoff starting at `initial_delay_seconds`
    (0.1s, 0.2s, 0.4s, 0.8s for the default `max_attempts=4`). Returns the acquired `Lockfile`,
    or `None` if every attempt lost the race."""
```

Used for `workspace.lock` (guarding `sessions.json` writes) and `workspaces.lock` (guarding
`projects.json` writes) — both short-lived, contended-but-quick locks, per this plan's design
notes ("try up to 4 times, start with 100ms delay and exponential backoff"). `session.lock`
acquisition is **not** retried anywhere — see "When a session claims its directory" and
"Restoring a session" below, both one-shot `try_acquire()` calls, since a locked session
directory means "another live process already owns this conversation," not "acquire once
contention clears."

### `workspace.lock`

`klorb.workspace.session_store.workspace_lock_path(workspace) -> project_history_dir(workspace) /
"workspace.lock"`. Every read-modify-write of `sessions.json` (recency bump, prune) acquires this
lock (via `acquire_lockfile_with_backoff`) for the duration of that one update, then releases it
immediately — this is a short critical section, not a long-lived per-process hold like
`session.lock`.

### `workspaces.lock`

`klorb.workspace.trust_manager` gains `workspaces_lock_path() -> KLORB_DATA_DIR /
"workspaces.lock"` (top-level, not per-project — `projects.json` itself is a single top-level
file). `TrustManager.register_project()`/`set_trusted()` each wrap their existing
load-mutate-save sequence in this lock (`acquire_lockfile_with_backoff`), closing the race two
concurrent klorb processes registering/trusting projects at once could otherwise hit (today's
`_load()`/`_save()` pair has no such protection at all).

### `session.lock`

Held by the `Session` object that owns a given `sessions/<subdir>/` directory (see "Session
lifecycle" below) for as long as that `Session` is live — not released between turns, only on
explicit close or process exit.

## Session title fallback

New function in `klorb.session_naming`:

```python
def fallback_session_title(prompt_text: str) -> str:
    """Derive a session title from `prompt_text` without calling the nano classifier: the first
    run of `/[a-zA-Z0-9_]+/` word-tokens in `prompt_text`, joined with single spaces, truncated
    to the first 6 tokens or 45 characters (whichever comes first), with a trailing "..."
    appended. Used when `generate_session_name()` returns `None` (classifier failure/timeout) or
    is skipped outright (e.g. a headless one-shot run that opts out of the classifier round
    trip) — see `Session._run_session_naming`."""
```

* Tokenization: `re.findall(r"[a-zA-Z0-9_]+", prompt_text)`.
* Word cap: stop after 6 tokens.
* Character cap: stop as soon as appending the next token (plus its separating space) would push
  the joined-so-far string past 45 characters; the partial token itself is never included, so the
  cutoff always lands on a whole-word boundary.
* Always suffixed with `"..."` (even when `prompt_text` is short enough that neither cap actually
  triggered — the caller only reaches this fallback when the *real* classifier title is
  unavailable, so the ellipsis is a permanent visual marker of "auto-derived, not
  classifier-derived," not a truncation indicator specifically).
* No tokens found (e.g. an all-punctuation/emoji prompt) → returns `"..."` alone; this is an
  accepted degenerate case, not specially handled.

`Session._run_session_naming` (`klorb.session.mixins.core`) calls this when
`generate_session_name()` returns `None`, setting `self.name` to the fallback exactly as it
would the classifier's own `title` on success — `set_id()`/slug-renaming is skipped in the
fallback case (there's no `SessionName.slug` to rename with), matching today's existing behavior
of leaving the random nonce id alone on a naming failure.

## Module layout

* `klorb/lockfile/__init__.py` — `Lockfile` protocol, `create_lockfile()` factory,
  `acquire_lockfile_with_backoff()`.
* `klorb/lockfile/_linux.py`, `klorb/lockfile/_macos.py` — platform implementations, imported
  lazily from the factory (not at module top), the same "delayed-import per platform" shape
  `klorb.tui.shell` already uses for POSIX-only subprocess plumbing.
* `klorb/workspace/session_store.py` — replaces `klorb/workspace/last_session.py` outright:
  `RecentSession`, `SessionsIndexState`, `sessions_dir()`, `sessions_list_path()`,
  `session_subdir_path()`, `session_state_path()`, `read_sessions_index()`,
  `touch_recent_session()` (recency bump + prune, lock-guarded), `write_session_state()`/
  `read_session_state()` (per-subdir `session.json`, same shape `write_last_session`/
  `read_last_session` had), `MAX_RECENT_SESSIONS`.
* `klorb/workspace/trust_manager.py` — gains `workspaces_lock_path()` and lock-guards its two
  mutators.
* `klorb/session/mixins/persistence.py` — new mixin, `SessionPersistenceMixin`, added to
  `Session`'s assembly in `klorb/session/__init__.py`. Owns:
  * `_session_lock: Lockfile | None`, `_session_subdir: str | None`, `_session_claimed: bool`
    (added in `SessionCoreMixin.__init__` alongside every other private field, per this repo's
    "all state initialized in `__init__`" convention — `SessionPersistenceMixin` itself has no
    `__init__` of its own, same pattern `SessionTurnsMixin` etc. already follow for a stateless
    mixin operating on `SessionCoreMixin`'s fields).
  * `claim_session_directory() -> None`: no-op if already claimed, or if
    `config.workspace.trusted` is `False` (same trust gate `last_session`/`input_history` already
    apply — an untrusted workspace never gets written to). Otherwise: `try_acquire()`s
    `session.lock` in `sessions/<self.id>/` one-shot (no retry — see "Locking"); on success,
    writes the initial `session.json`, calls `touch_recent_session()`, and marks
    `_session_claimed = True`. On failure (lost the one-shot race — vanishingly unlikely given
    `Session.id`'s timestamp+nonce uniqueness, but possible), logs a warning and leaves
    `_session_claimed = False`: this `Session` simply never persists (every `persist_state()`
    call becomes a no-op), rather than raising and interrupting the turn that triggered it.
  * `persist_state() -> None`: calls `claim_session_directory()` first (a no-op if already
    claimed or the workspace is untrusted), then — only if claimed — (re)writes `session.json`
    and calls `touch_recent_session()` again (so the title, if it changed since the directory was
    first claimed, and the recency position both stay current).
  * `close()` (already defined on `SessionCoreMixin` — extended, not overridden a second time):
    before running teardown callbacks, if `_session_claimed`, calls `persist_state()` one last
    time, then `_session_lock.release()`, and clears `_session_claimed`/`_session_lock`/
    `_session_subdir` — "write the history one last time, update the list, then release the
    lock," exactly as this plan's design notes specify for an explicit close (clear-session, ACP
    `session/new` replacing a prior session, ACP `close()` on disconnect). Idempotent: a second
    `close()` call is a no-op past the first, matching `SessionCoreMixin.close()`'s existing
    idempotency contract.

  **No `atexit`-registered backstop.** This plan's design notes ask for the lock to be "cleaned
  up as the process exits," which reads as an `atexit.register(self.close)` call — deliberately
  *not* implemented that way. Every real entry point already calls `close()`/`persist_state()`
  explicitly on its own way out (the TUI's `action_quit`, its crash/force-exit paths,
  `klorb.cli.main()`'s one-shot path, `KlorbAcpAgent.close()`), so an `atexit` hook is pure
  redundancy for production use — and it is actively harmful for the test suite, which
  constructs many short-lived `Session`s that are never explicitly closed by design: each one's
  registered callback would keep that `Session` alive for the rest of the test process (a
  leak), and would fire its write at interpreter shutdown *after* `pytest`'s `monkeypatch`
  fixtures have already reverted every test's `$KLORB_DATA_DIR` isolation — writing real session
  files into the developer's actual data directory. This is exactly the failure mode this
  plan's implementation hit and removed. "Process exit" persistence is instead satisfied purely
  by each real entry point's own explicit call, per "When `persist_state()` is called" below.

### When a session claims its directory

`claim_session_directory()` is called from exactly two places, both *after* the point where
session naming has already run or been bypassed for this session — per this plan's design notes,
data must never land in a session-id-specific directory before the id's final slug (if any) is
decided:

1. `SessionTurnsMixin.send_turn()`, immediately after the `self._session_naming_pending` block
   (whether the classifier ran and renamed `self.id`, ran and fell back to
   `fallback_session_title()`, or was already `False` because this is a restored session) —
   i.e. right before building this turn's `user_message`.
2. `persist_state()` itself calls it first (see above), for every call site *other* than the
   first turn — covering "end of turn" and "process exit" persistence below, where naming has
   necessarily already resolved on some earlier turn.

### When `persist_state()` is called

* **End of turn**, stop reason "finished" or "interrupted": the TUI's `_finish_turn()`
  (`klorb.tui.mixins.prompt_submission`) calls `self._session.persist_state()` unconditionally —
  this method already runs on every path a turn (or shell command) ends, success, error, or
  abort alike, so no new call site is needed there, just this one added call. The ACP server's
  `KlorbAcpAgent.prompt()` (`klorb.server.klorb_agent`) calls `self._session.persist_state()`
  right before returning, on both the `ResponseAborted` ("cancelled") and normal ("end_turn")
  paths.
* **Process exit, unconditionally, no modal**: `klorb.tui.mixins.key_actions.action_quit`
  is simplified — the `SaveOnQuitScreen` confirmation dialog is removed entirely (see "TUI
  changes" below); quitting just calls `self._session.close()` (which persists + releases the
  lock, per above) and proceeds to `_begin_exit()`. The crash-save path (`_handle_repl_crash`)
  and the force-exit path (`_collect_hang_diagnostics`) both switch from calling
  `write_last_session` directly to calling `self._session.persist_state()` (crash/force-exit
  paths call `persist_state()`, not `close()` — the lock is deliberately *not* released on an
  abnormal exit, so a concurrently-running process, if any, can't mistake a
  crashed-but-not-yet-reaped session for a cleanly closed one; the OS reclaims the underlying
  file descriptor's lock when the process's file descriptors are closed regardless).
  `klorb.cli.main()`'s headless one-shot path already calls `session.close()` unconditionally at
  the end of its run, and `KlorbAcpAgent.close()` already calls `Session.close()` — both existing
  call sites needed no change to pick up the new persist-then-release behavior. See "No
  `atexit`-registered backstop" above for why this plan relies on these explicit calls rather
  than a single process-exit hook.

## TUI changes

* `klorb.tui.panels.confirm_screen.SaveOnQuitScreen` and its use in `action_quit` are removed —
  quitting the TUI (Ctrl+Q, `:q`/`/quit`/`/exit`, the "Quit the application" system command) no
  longer asks whether to save; it always does (via `Session.close()`, see above).
  `ConfirmScreen` itself (the plain yes/no modal used elsewhere — workspace trust prompts) is
  unaffected.
* `klorb.tui.mixins.workspace_bootstrap._maybe_restore_last_session` is replaced by
  `_maybe_restore_latest_session`: reads `sessions.json` for the resolved (trusted) workspace,
  takes the entry at index 0 (if any), and — mirroring "Restoring a session" below — attempts a
  one-shot `session.lock` acquisition on its subdirectory; on success, restores exactly as today
  (rebuild `Session` from the saved `config`/`messages`/etc., re-render history); on failure (the
  entry is locked, or `session.json`/the subdirectory itself is missing/corrupt — the same
  `ValidationError`-tolerant handling `read_last_session` already had), falls back to the
  already-constructed fresh `Session` exactly as if no saved session existed, per this plan's
  design notes ("If it's locked or deleted... starts a new session").
* New palette command, **"Load session"** (`klorb.tui.commands.session_commands`, alongside
  `CLEAR_SESSION_LABEL`/`SHOW_SESSION_STATS_LABEL`), opening a new `LoadSessionScreen`
  (`klorb.tui.commands.session_commands`, following `ThemeSelectionScreen`'s exact shape — a
  `ModalScreen[None]` with a header `Static` plus an `OptionList` of titles, `escape` bound to
  `dismiss`, up/down handled natively by `OptionList`, Enter firing `on_option_list_option_
  selected`). Listing order matches `sessions.json`'s own order (most recent first); an entry
  with no title falls back to displaying its `session_id`. Selecting an entry: if it's the
  *currently loaded* session, no-op-dismiss; otherwise closes the live session
  (`self._session.close()` — persisting and releasing its own lock first, exactly like "clear
  session" does) and attempts to load the selected one the same one-shot-lock way
  `_maybe_restore_latest_session` does, falling back to a fresh blank session (with a `show_
  notice` explaining why) if the target is locked or missing by the time the user picks it.

## ACP server changes

`klorb.server.klorb_agent.KlorbAcpAgent`'s stubbed `list_sessions`/`load_session` become real
(scoped to this plan's needs — a full generic ACP resume/fork feature is still out of scope, per
`docs/specs/klorb-server.md`'s existing "not implemented at this checkpoint" framing for
`fork_session`/`resume_session`, which stay stubbed):

* `list_sessions(cwd, cursor=None, ...)`: resolves the workspace for `cwd` (`TrustManager.
  resolve_workspace`, read-only — never bootstraps/registers), reads its `sessions.json`, and
  returns an ACP `ListSessionsResponse` built from its `RecentSession` entries (id + title;
  ACP's own `SessionInfo` shape). No pagination beyond what `sessions.json` already caps at
  (`MAX_RECENT_SESSIONS`, or slightly more when locked entries are being kept past the cap) — a
  `cursor` is accepted but always ignored, returning everything in one page, since the list is
  small by construction.
* `load_session(cwd, session_id, mcp_servers, ...)`: resolves the workspace, looks up
  `session_id` in its `sessions.json`, and — exactly like the TUI's `_maybe_restore_latest_session`
  — attempts a one-shot `session.lock` acquisition on that entry's subdirectory. On success,
  replaces `self._session` (closing any prior live one first, same as `new_session()`) with a
  `Session` rebuilt from the saved state, snapshots `self._acp_session_id` to the (possibly
  since-renamed) restored id, and returns `LoadSessionResponse` (mirroring `new_session()`'s
  reply shape: `modes`, `field_meta.klorb.{workspace,title}`). On failure (locked or missing),
  raises `acp.RequestError.invalid_params({"reason": "session is locked or no longer exists"})`
  — per this plan's design notes, it's the *client's* job to fall back to starting a new session
  when a load fails, not the server's.
* `initialize()`'s advertised `AgentCapabilities` gains `field_meta={"klorb": {..., "loadSession":
  True}}` (alongside the existing `sessionConfig`/`sessionStats`/etc. flags) so a client can tell
  whether it's talking to a server new enough to support this — `docs/specs/klorb-server.md`'s
  existing `_client_supports()`/capability-flag convention, applied here on the *agent*'s side
  (capabilities the agent offers) rather than the client's.
* `KlorbAcpAgent.close()` (already calls `self._session.close()`) needs no change — `Session.
  close()` already persists + releases the lock, per "Session lifecycle" above.

## VS Code plugin changes

* New setting, `klorb.resumeLatestSession` (boolean, default `true`), added to `package.json`'s
  `contributes.configuration` alongside `klorb.serverPath`/`klorb.configPath`. When `true`,
  opening the panel calls `session/load` for the workspace's most-recent session id (from a new
  `globalState` entry — see below) before falling back to `session/new`; when `false`, always
  starts with `session/new`.
* `vscode-plugin/src/host/features/acp/acpConnection.ts`'s `AcpConnection` gains a
  `loadSession(cwd, sessionId)` method (parallel to the existing `newSession(cwd)`), calling the
  ACP client's `session/load`. On an `invalid_params` error (the failure mode `load_session`
  raises above), the caller falls back to `newSession(cwd)` exactly as this plan's design notes
  specify ("If it's locked or deleted, the acp server indicates the failure to the plugin which
  then starts a new session").
* New `globalState` entry (the extension host's first use of `context.globalState` — today
  nothing is persisted there at all): `klorb.lastSessionId.<workspace-path-hash>`, written
  whenever a `session/new`/`session/load` response arrives, read at activation time (when
  `resumeLatestSession` is enabled) to decide whether/what to resume.
* New top-row icon pair, alongside/near the existing `StatusMenu` chevron
  (`vscode-plugin/src/webview/components/StatusRow.tsx`/`StatusMenu.tsx`): a speech-bubble-plus
  icon (`title="New session"`) and a stopwatch icon (`title="Session history"`), both
  `<vscode-icon>` elements so VS Code renders their native tooltip from the `title` attribute.
  * **New session**: clears the webview's `entries: HistoryEntry[]` (`historyModel.ts`) and sends
    the existing `newSession` webview→host message, which already drives `AcpConnection.
    newSession()` — no protocol change needed here, just wiring the icon to the same path
    `StatusMenu`'s existing "New Session" item already uses.
  * **Session history**: sends a new `listRecentSessions` webview→host message; the host answers
    with a new `recentSessionsResult` host→webview message carrying `{id, title}[]` (mirroring
    `RecentSession`, JSON-cased per this repo's existing webview-protocol convention — the
    existing `shared/webviewMessages.ts` types already use camelCase field names, since that
    layer is TypeScript-to-TypeScript, not filesystem JSON, so this is *not* the same "match the
    sibling file" reasoning as `sessions.json` above). The webview shows the titles in a VS Code
    `showQuickPick` (via the same `vs.showQuickPick` injection seam `sessionControls/commands.ts`
    already uses for model/thinking pickers); selecting one sends `resumeSession {id}`, which
    calls `AcpConnection.loadSession()`.
* New host→webview message, `sessionReplay { entries: HistoryEntry[] }`: sent once after a
  successful `session/load` (both the resume-latest-on-open case and an explicit "Session
  history" pick), carrying a full reconstruction of the loaded session's conversation for
  `historyModel.ts`'s reducer to seed `entries` wholesale — the same "replace state outright"
  shape `sessionReset` already uses, just populated rather than emptied. Built server-side by a
  new mapping in `klorb.server.update_mapping` from the restored `Session.messages` (reusing the
  same message→ACP-update translation `TurnBridge` already has for `on_chunk`/`on_tool_call`,
  applied to history instead of a live stream) — tool-call rendering reuses the same best-effort
  reconstruction `klorb.tui.ReplApp._render_restored_tool_call` already implements for the TUI's
  own restore path (ported to `update_mapping`, not duplicated — see docs/specs/session-
  persistence.md's "Reconstructing a tool call's display" section for the encoding this must
  reverse). Per this plan's design notes, this replay is sent for *every* successful load
  (including resume-latest on extension activation), and the webview always applies it over
  whatever cached `entries` the webview's own `sessionState` persistence already restored
  (`webview/features/sessionState/index.ts`) — the cached state is shown immediately
  (stale-while-revalidate), then replaced once the replay arrives, accepting the loss of
  non-conversation cached items (e.g. a cached session-stats card) noted in this plan's design
  notes.
* A `session/load` (or the initial `session/new`) always yields an **empty** `entries` array for
  a genuinely new session — no change needed here; this already falls out of "the webview clears
  `entries` when it sends `newSession`" above.

## Testing

* `klorb/tests/lockfile/` — acquire/release/contention/`is_held_by_other` behavior for whichever
  platform implementation `create_lockfile()` resolves to in CI, plus `acquire_lockfile_with_
  backoff`'s retry/backoff/give-up behavior against a pre-locked file.
* `klorb/tests/workspace/test_session_store.py` — `RecentSession`/`SessionsIndexState`
  round-trip, `touch_recent_session` recency-bump and pruning (including the "skip a locked
  entry past the cap" behavior), `write_session_state`/`read_session_state` (mirroring the
  existing `last_session` tests being moved/renamed here).
* `klorb/tests/session_naming/` — `fallback_session_title` word/character-cap edge cases (short
  prompt, exactly-6-words, exactly-45-chars, no matching tokens at all).
* `klorb/tests/session/test_persistence_mixin.py` — `claim_session_directory`/`persist_state`/
  `close` against an untrusted workspace (no-op throughout), a trusted one (claims, persists,
  releases), and a lost one-shot lock race (never claims, every `persist_state` a no-op).
* `klorb/tests/server/test_klorb_agent.py` (existing file, extended) — `list_sessions`/
  `load_session` happy path, locked-session `invalid_params` failure, unknown-session failure.
* `klorb/tests/tui/` (existing suite, extended) — `LoadSessionScreen` selection, quit-without-modal
  now always persisting, restore-falls-back-to-blank-on-lock-contention.
* VS Code: `vscode-plugin/test/host/` and `test/webview/` gain coverage for the new icons/quickpick/
  `sessionReplay` reducer path, following this repo's existing Vitest conventions for those trees.

## Follow-up items (`TODO.md`)

Logged under `### Plan 017: Multiple sessions per workspace` once this plan is implemented:

* Surface a "delete this saved session" action (TUI palette command and/or VS Code quickpick
  item) rather than relying solely on `MAX_RECENT_SESSIONS` pruning to reclaim space.
* Show a relative recency timestamp ("2 hours ago") in the Load Session picker — today's design
  deliberately has no timestamp field on `RecentSession`, only list order; adding one is a
  backwards-compatible schema bump if wanted later.
* `docs/specs/klorb-server.md`'s `fork_session`/`resume_session` stubs are unaffected by this
  plan; a future plan could build genuine session forking on top of this same `sessions/`
  directory layout.
