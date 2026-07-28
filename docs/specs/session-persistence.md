# Session persistence

## Summary

A trusted workspace keeps up to `MAX_RECENT_SESSIONS` recent conversations on disk, each in its
own subdirectory, indexed by recency in one `sessions.json`. Every turn's end — success, error,
or interruption alike — persists the active `Session`'s `SessionConfig`, full message history,
and running statistics; quitting the TUI, an unhandled crash, and a force-exit all do the same,
unconditionally and without any confirmation prompt. Opening klorb again in the same trusted
workspace auto-restores the most recently touched session, re-rendering the history scroll to
match; the TUI's "Load session" palette command and the ACP server's `session/load` let a user
switch to a different saved session explicitly. `klorb.workspace.session_store` owns the on-disk
format and the recency index; `klorb.session.mixins.persistence.SessionPersistenceMixin` (part of
`Session` itself) owns *when* state hits disk and the per-session lock that arbitrates which
process currently owns a given saved session; `klorb.session.restore.try_restore_session` is the
one shared path every caller (TUI, ACP server) uses to rebuild a `Session` from a saved one.

## How it works

### Where things live

```text
$KLORB_DATA_DIR/projects/<token>-<basename>/
├── history                       # unchanged: append-only prompt-recall log
├── workspace.lock                # guards read-modify-write of sessions.json
└── sessions/
    ├── sessions.json             # ordered index of recent sessions (most-recent first)
    └── <subdir>/
        ├── session.json          # one saved session's full state
        └── session.lock          # held by the live process that owns this session, if any
```

`sessions/` lives in the same per-project directory as the prompt-input history file
(`klorb.workspace.input_history.project_history_dir`), under `$KLORB_DATA_DIR`, not inside the
workspace itself — the same reasoning docs/adrs/store-last-session-under-klorb-data-dir-not-
workspace.md gives for the single-slot `last-session.json` design this replaces. `<token>` is the
registered project's uuid, or a stable hash of the canonical workspace path for an
unregistered-but-trusted workspace, so both kinds of trusted workspace get one consistent parent
directory. `sessions/` and each `sessions/<subdir>/` are created lazily, exactly like
`project_history_dir` itself — nothing is written for an untrusted or unresolved workspace.

`klorb.workspace.session_store` defines the on-disk shapes:

* `SessionsIndexState` (`sessions.json`, schema `"klorb-session-list"`) — `recent_sessions:
  list[RecentSession]`, most-recently-touched first.
* `RecentSession` — `session_id`, `subdir`, `title`, `aliases`. `subdir` is set once, to the
  session's *original* `Session.id`, when its directory is first created, and never renamed
  afterward; it's a saved session's stable identity, independent of `session_id`. `session_id`
  mirrors the live `Session.id`, which the naming classifier may rename in place (see "Session
  naming" below) — when that happens, the existing `sessions.json` entry is updated to the new
  `session_id` rather than duplicated, keyed by matching `subdir`, not the (possibly stale) old
  `session_id`. `aliases` lists every prior `session_id` this entry was renamed *from* (oldest
  first) — so a caller holding a pre-rename id (e.g. a client that recorded the id `session/new`
  returned before the classifier renamed it) can still resolve the same session via
  `find_recent_session`. `title`
  mirrors `Session.name`.
* `SessionState` (`session.json`, schema `"klorb-session"`) — `config` (`SessionConfig`),
  `messages`, `statistics` (`SessionStatistics | None`), `session_id`, `root_id`, `session_name`,
  `cur_chainlink_task_id`. The same shape the old single-slot `last-session.json` carried, plus
  `root_id`/`cur_chainlink_task_id` (see docs/specs/chainlink-task-tracking.md) — absent (`None`)
  in files written by an older klorb version that predates either field.

Both files are schema-enveloped per docs/specs/persisted-json-schema-versioning.md.

### Locking (`klorb.lockfile`)

`klorb.lockfile.create_lockfile(path)` returns a non-blocking, whole-file exclusive `Lockfile` for
`path`: an Open-File-Description lock (`klorb.lockfile._linux.OfdLockfile`, `fcntl.F_OFD_SETLK`)
on Linux, or a classic POSIX record lock (`klorb.lockfile._macos.PosixLockfile`,
`fcntl.F_SETLK`) on macOS, where OFD locks aren't available; `NotImplementedError` on any other
platform (Windows isn't supported yet). `try_acquire()` never blocks — it returns `False`
immediately if another process already holds the lock, rather than waiting. `is_held_by_other()`
probes the lock without attempting to acquire it, for a "does anyone else already own this?"
check that doesn't perturb a lock this process doesn't hold. The OS releases a held lock
automatically when its holding process's file descriptors close (including on a crash), but a
clean shutdown must still call `release()` explicitly.

Three lockfiles arbitrate the three kinds of shared state this feature touches:

* **`session.lock`** (one per `sessions/<subdir>/`) — held by whichever live process currently
  owns that saved session, for as long as that process keeps the session open. Acquired
  *once*, one-shot (`try_acquire()`, never retried): a locked `session.lock` means another live
  process already owns that directory, not "wait for it to free up" — see
  `SessionPersistenceMixin.claim_session_directory`/`klorb.session.restore.try_restore_session`
  below.
* **`workspace.lock`** (one per per-project directory, guarding `sessions.json`) and
  **`workspaces.lock`** (one per `$KLORB_DATA_DIR`, guarding `projects.json` —
  `klorb.workspace.trust_manager.TrustManager`) — both short-lived, held only for the duration of
  one read-modify-write of their respective index file. Acquired via
  `klorb.lockfile.acquire_lockfile_with_backoff`, which retries contention up to 4 attempts total
  with exponential backoff starting at 100ms (100ms, 200ms, 400ms, no delay after the last
  attempt), returning `None` if every attempt loses the race. A caller that gets `None` logs a
  warning and skips the update rather than raising — a missed recency-index update or trust-file
  write is recoverable (the next call tries again) and must never interrupt the turn or command
  that triggered it.

### Session titles

A session's `title`/`Session.name` comes from one of two sources, both resolved on the session's
first turn (see docs/specs/session-and-turns.md's "Session naming" section for the full
mechanism): the nano classifier (`klorb.session_naming.generate_session_name`) on success, or —
on any failure (timeout, unavailable classifier, malformed reply) —
`klorb.session_naming.fallback_session_title(prompt_text)`: the first `MAX_FALLBACK_TITLE_WORDS`
(6) `[a-zA-Z0-9_]+` words of the prompt, space-joined, capped at `MAX_FALLBACK_TITLE_CHARS` (45)
characters (whichever limit is reached first), followed by `"..."`. Every session that survives
past its first turn therefore ends up with a real title — never a raw id or a generic
placeholder — by the time it's ever saved to disk.

### Claiming a session directory (`SessionPersistenceMixin.claim_session_directory`)

A `Session` claims its `sessions/<subdir>/` directory — acquiring `session.lock`, writing the
first `session.json`, and adding the `sessions.json` recency entry — from
`SessionCoreMixin._run_session_naming`'s caller in `SessionTurnsMixin.send_turn()`, right after
session naming has resolved on the first turn (whether the classifier renamed `self.id`, fell
back to `fallback_session_title`, or was already skipped because this is a restored session).
Data must never land in a session-id-specific directory before the id's final form is decided —
`subdir` defaults to `self.id` at claim time, so claiming before naming resolves would key the
directory off a since-discarded random-nonce id. A no-op if already claimed, or if
`config.workspace.trusted` is `False` (the same trust gate the input-history store always
applied); losing the one-shot lock race (vanishingly unlikely, given `Session.id`'s
timestamp+nonce uniqueness) logs a warning and leaves the session unclaimed for the rest of its
life — every later `persist_state()` call becomes a no-op.

A *restored* session (see "Restoring" below) skips this claim path entirely:
`adopt_claimed_session_directory(subdir, lock)` hands it an already-acquired `session.lock` and
`subdir` directly, since a restored session's on-disk `subdir` can differ from its (possibly
since-renamed) `self.id`.

### Persisting (`SessionPersistenceMixin.persist_state()` / `close()`)

`persist_state()` claims the session's directory first (a no-op if already claimed or the
workspace is untrusted), then writes `session.json` and refreshes the session's `sessions.json`
recency entry (moves it to the front). Called:

* At the end of every turn (`klorb.tui.mixins.prompt_submission.PromptSubmissionMixin.
  _finish_turn`, `klorb.server.klorb_agent.KlorbAcpAgent.prompt`), success, error, or
  interruption alike — this is what makes "quit without saving" a non-concept: state is already
  current on disk by the time any exit path runs.
* By the TUI's force-exit/hang-diagnostics path (`KeyActionsMixin._collect_hang_diagnostics`),
  deliberately calling `persist_state()` rather than `close()` — the process is about to
  `os._exit`, so `session.lock` is left held on purpose rather than released, so a
  concurrently-running process can't mistake a wedged-but-not-yet-reaped session for a cleanly
  closed one. The OS reclaims the lock regardless once this process's file descriptors close.
* By the TUI's crash path (`klorb.tui.entrypoint._handle_repl_crash`), same reasoning as above
  but via the app's live `_session` at crash time (which may differ from whatever `Session`
  `run_repl()` started with — `/clear` or a session load can have replaced it).

`Session.close()` calls `_finalize_session_persistence()` before running its other teardown
callbacks: writes one final `session.json`, then releases `session.lock` — "close" always means
"persist one last time, then let go of the lock." Called by:

* `ReplApp.action_quit` (Ctrl+Q, and the "Quit the application" system command) — no confirmation
  prompt; session state is always persisted on quit, unconditionally.
* `clear_session()`/`load_recent_session()` on the outgoing session, before replacing it.
* `KlorbAcpAgent`'s `session/new` and `session/load` handlers, on any existing live session,
  before replacing it.
* `klorb.cli.main()`'s headless one-shot path, at the end of the run.

A no-op if the session was never claimed (untrusted workspace, or a session that never survived
past its first turn's naming step) — matching `close()`'s own idempotency contract, a second call
is also a no-op.

There is deliberately no `atexit`-registered backstop for any of this: every real entry point
already calls `close()` or `persist_state()` explicitly on its own way out, so an `atexit` hook
would be pure redundancy in production while actively harmful for tests, which construct many
short-lived `Session`s that are mostly never explicitly closed — an `atexit` hook would leak every
claimed test `Session` for the rest of the process and fire its writes at interpreter shutdown,
after test fixtures have already reverted any filesystem isolation, writing real files into the
developer's actual `$KLORB_DATA_DIR`.

### Pruning (`MAX_RECENT_SESSIONS`)

`touch_recent_session()` — the function `persist_state()`/`close()` call to refresh a session's
recency entry — prunes `sessions.json` down to `MAX_RECENT_SESSIONS` (30) entries every time it
writes a longer list: entries past the cap (least recently touched first) have their
`sessions/<subdir>/` directory deleted (`shutil.rmtree`) and are dropped from the index, *except*
any entry whose `session.lock` is currently held by another live process
(`create_lockfile(...).is_held_by_other()`) — that entry is kept regardless, so the list can
legitimately exceed the cap while several sessions are simultaneously open across different
processes. This whole read-modify-write (read the index, re-key/insert the touched entry, prune,
write) runs under `workspace.lock`.

### Restoring the most recent session at startup

The TUI's `WorkspaceBootstrapMixin._maybe_restore_latest_session(workspace)` runs from
`_resolve_workspace_trust()` once the workspace is resolved, immediately after attaching the
input-history store, and only when the resolved workspace is trusted. This runs before any
`initial_message` is submitted, so a `klorb -m "..."` invocation's message becomes the next turn
of the restored conversation rather than racing it.

If `workspace`'s `sessions.json` has no entries yet, this is a no-op — the freshly-constructed
`Session` stays as-is. Otherwise it attempts to lock and rebuild the top (most-recently-touched)
entry via `klorb.session.restore.try_restore_session`; if that fails (the entry's `session.lock`
is held by another live process, or its `session.json` is missing or fails to validate), this is
also a no-op — a corrupted or contended save is "nothing to restore," not a startup error. On
success, the freshly-constructed `Session` is closed and replaced with the restored one
(`WorkspaceBootstrapMixin._adopt_restored_session`).

The ACP server has no equivalent auto-restore on connect — a client that wants to resume a
specific past session calls `session/load` explicitly (see "ACP server" below); `KlorbAcpAgent`
always starts with a fresh session from `session/new` otherwise.

### Restoring a specific session (`klorb.session.restore.try_restore_session`)

Shared by every caller that resumes a *specific* saved session — the TUI's restore-latest flow
above, its "Load session" picker, and the ACP server's `session/load`:

```python
def try_restore_session(
    workspace: Workspace, entry: RecentSession, *,
    provider: ApiProvider, model_registry: ModelRegistry, process_config: ProcessConfig,
) -> Session | None: ...
```

1. Acquires `entry`'s `session.lock` (one-shot `try_acquire()`, never retried, same reasoning as
   claiming). Returns `None` immediately on failure, with no other side effect.
2. Reads `entry.subdir`'s `session.json` via `read_session_state`. Returns `None` (releasing the
   lock first) if the file is missing or fails to validate as `SessionState` — a hand-edited or
   otherwise corrupted file — the same fail-open contract the predecessor design had for a single
   save file.
3. The saved `SessionConfig` is copied with `workspace` overridden to the just-resolved
   `Workspace` (not the one recorded at save time) — trust and registration state are always
   taken fresh, never from the save file itself. No other saved field is reconciled against
   whatever config layers would produce for a brand-new session; the restored session's settings
   (model, thinking, permission rules, etc.) win outright, the same way `/clear` winning over a
   config file's declared defaults works elsewhere (see docs/specs/process-and-session-config.md).
4. A new `Session` is built from the restored config, keeping the saved `session_id`/`root_id`/
   `session_name`/`cur_chainlink_task_id` and loading the saved `messages`/`statistics` onto it
   (`load_messages`/`load_statistics`) — safe to call before any `send_turn()`, since a
   `role="system"`/`"tool_defs"` bookkeeping message already present is left as-is rather than
   duplicated, and neither is ever replayed to the model anyway (the live system prompt and tool
   definitions are always resolved fresh and sent out-of-band on every turn). `Session.__init__`
   seeds `session_naming_pending` directly from whether `session_name` was passed in, so a
   restored, already-named session never re-triggers the classifier on its next prompt.
5. `session.adopt_claimed_session_directory(entry.subdir, lock)` hands the already-acquired lock
   straight to the new `Session` — the caller never calls `claim_session_directory()` itself.

The caller is responsible for closing whatever session it's replacing, and for rendering the
restored history into its own UI.

### TUI history re-render (`_mount_restored_history` / `_render_restored_tool_call`)

Both the startup restore and the "Load session" picker re-render every restored message into the
history scroll, in order, via the same `_mount_response_widget`/`_mount_thinking_widget`/
`_mount_tool_call_widget` helpers a live turn uses, so a restored conversation looks the same as
it would have live:

* `role="user"` → a `.prompt` `Static`, matching `_submit_prompt`'s echo.
* `role="assistant"`/`"thinking"` → `_mount_response_widget`/`_mount_thinking_widget`, each
  `*(interrupted)*`-suffixed when `processing_state == "aborted"`.
* `role="tool_use"` → one `_mount_tool_call_widget` per `ToolCallRequest`, rendered via
  `_render_restored_tool_call` — a best-effort reversal of `Session._run_tool_calls`'s persisted
  encoding: `call.arguments` re-parsed as JSON (a decode failure renders the same
  "Invalid JSON in tool call arguments: ..." a live malformed call would); the matching
  `role="tool_response"`'s `content` is decoded as a JSON `ToolResponseEnvelope`
  (`is_error`/`error_message`/`response_body`) when possible, else `"Error: "`-prefix-matched
  against a pre-envelope save, else treated as a plain successful string. Both branches hand off
  to `_render_tool_result` (shared with the live path), which instantiates the named tool via
  `ToolRegistry` for its own `summary()`/`detail_view()`, or falls back to the shared default
  formatters if the tool isn't currently registered. This is deliberately best-effort, not
  lossless — see the old design's same caveat: a successful string result that happens to start
  with `"Error: "` is indistinguishable from an actual failure once folded into `content`.
* `role="system"`/`"tool_defs"`/`"tool_response"` are never mounted on their own, matching how
  they're never rendered live either.

A final `.notice` ("Restored previous session (`N` messages).") is mounted after the replay, and
the history is scrolled to the end.

### TUI: "Load session" picker

The `SessionCommandProvider` command palette provider (reachable via `ctrl+p` or `>load session`
in the prompt, alongside "Clear session"/"Show session stats") offers "Load session", which pushes
`klorb.tui.commands.session_commands.LoadSessionScreen` — a modal listing
`ReplApp.list_recent_sessions()`'s entries (`sessions.json`'s own recency order, most-recent
first) by title (falling back to the raw `session_id` for an untitled entry) in a Textual
`OptionList`, mirroring `ThemeSelectionScreen`'s shape: arrow keys to move, Enter to select
(`ReplApp.load_recent_session(entry)`), Escape to dismiss without a selection.

`load_recent_session(entry)` is a no-op if `entry` is already the live session; otherwise it
closes the outgoing session (persisting and releasing its own lock, same as `clear_session()`),
clears the history scroll, and attempts `try_restore_session`. On failure (locked by another
process, or the save vanished/corrupted between listing and picking), it reports why via
`show_notice(..., error=True)` and leaves a fresh, blank `Session` in place — the same fallback a
brand-new `ReplApp` would have built anyway had no saved session existed at all. On success it
adopts the restored session the same way startup restore does
(`WorkspaceBootstrapMixin._adopt_restored_session`).

### ACP server

`KlorbAcpAgent.initialize()` advertises `AgentCapabilities(load_session=True,
session_capabilities=SessionCapabilities(list=SessionListCapabilities()))` — the stable top-level
`session/load` flag plus the (still protocol-unstable) `session/list` capability;
`AcpServer.run()` passes `use_unstable_protocol=True` to `acp.run_agent()`, since the Python ACP
SDK's own request router gates any method marked `unstable=True` (including `session/list`)
behind that flag independently of what the agent itself advertises.

* **`session/load`** (`load_session(cwd, mcp_servers, session_id)`) — looks up `session_id` in
  `sessions.json` via `find_recent_session`, which matches either the entry's current
  `session_id` or any of its `aliases` (the prior ids it was renamed from — see `Session.aliases`),
  so a caller holding a pre-rename id still resolves the same session. On failure,
  `cwd`'s workspace `sessions.json`, raising `invalid_params` if it isn't present at all. On a
  hit, calls `try_restore_session`; raising `invalid_params` (`"session is locked or no longer
  exists"`) if that returns `None` — per this method's design, it's the *client*'s job to fall
  back to `session/new` on a failed load, not this server's. On success, closes any existing live
  session, adopts the restored one, sends a `_klorb/sessionReplay` ext notification (see below)
  reconstructing the conversation for the client, and returns a `LoadSessionResponse` carrying the
  restored session's mode state and (`field_meta.klorb`) workspace/title info, mirroring
  `session/new`'s reply shape. `mcp_servers` is accepted but never acted on, same as `session/new`.
* **`session/list`** (`list_sessions(cursor, cwd)`) — returns `cwd`'s workspace's
  `sessions.json` entries, most-recently-touched first, as `SessionInfo(cwd, session_id, title)`.
  `cursor` is accepted but always ignored, and `next_cursor` always omitted, since the list is
  small by construction (`MAX_RECENT_SESSIONS`) — everything fits in one page. Raises
  `invalid_params` if `cwd` is omitted: unlike `session/new`/`session/load`, ACP allows a client to
  omit it, but klorb has no process-wide "current workspace" to fall back to outside of one
  already-live session.
* **`session/new`** keeps its existing "replace, don't add" semantics — tearing down any existing
  live session via `Session.close()` (persisting it one last time) before building a fresh one.

#### `_klorb/sessionReplay`

`klorb.server.update_mapping.build_session_replay(session, tool_registry, workspace_root)` builds
the `entries` payload sent as `{"sessionId": ..., "entries": [...]}` on a successful
`session/load`: one dict per restored message, in the webview's own `HistoryEntry`-adjacent shape
— `role="user"`/`"assistant"`/`"thinking"` become `{"kind": "prompt"/"response"/"thinking",
"text", "streaming": false}`; `role="system"`/`"tool_defs"` are skipped; `role="tool_use"` becomes
one `{"kind": "toolCall", "callId", "status", "title", "toolKind", "locations", "contentText",
"expanded": false}` per `ToolCallRequest`, built by `_replay_tool_call_entry` — the same
best-effort reversal of the persisted tool-response encoding `_render_restored_tool_call` applies
for the TUI, reusing the same `locations`/`toolKind`/`title` helpers a live `tool_call` update
uses. This loses non-conversation history items a live session might have accumulated (e.g. the
session-stats grid) — an accepted degenerate case, not specially handled.

### VS Code plugin

* **`klorb.resumeLatestSession`** (boolean, default `true`) — on connect, if enabled and a
  previously-used session id is stored (`context.globalState`, keyed by workspace folder), the
  extension tries `AcpConnection.loadSession(cwd, sessionId)` before falling back to
  `session/new` on any failure (an invalid/locked/deleted session). The connected session's id is
  remembered (`rememberLastSessionId`) after every successful `session/new`/`session/load`, so the
  next VS Code window opened against the same workspace resumes it.
* **Panel header icons** — `PanelHeader` renders two icon buttons to the right of the session
  title: a speech-bubble-plus (`comment-add`) for "New session" (posts `newSession`, same as the
  existing status-menu action — closes the current session server-side and starts a blank one,
  with an empty webview history), and a stopwatch (`watch`) for "Session history" (posts
  `listRecentSessions`, which the extension host answers by running `klorb.browseSessions`).
* **`klorb.browseSessions`** — fetches the workspace's saved sessions via
  `AcpConnection.listSessions(cwd)` (`session/list`), shows them in a native
  `vscode.window.showQuickPick` (title or raw id as the label, `"current"` description on the
  live session), and on a pick that isn't the already-live session, calls
  `AcpConnection.loadSession(cwd, pickedId)` and updates the remembered last-session id.
  Deliberately does not clear the webview history before the load completes — the incoming
  `_klorb/sessionReplay` notification replaces it wholesale once the server confirms the load, so
  clearing early would risk a visible flash of an empty panel racing against (or, on failure,
  outliving) that notification.
* **`sessionReplay` webview message** — the host relays a `_klorb/sessionReplay` ext notification
  straight through as a `{type: 'sessionReplay', entries}` host→webview message;
  `applySessionReplay` (`webview/features/history/historyModel.ts`) replaces the entire history
  array with the replayed entries wholesale, the same wholesale-replace shape `applyHostMessage`
  uses for every other full-history-affecting message. The same replay happens on the initial
  resume-latest load, not just an explicit "Session history" pick, so the webview's history always
  reflects the server's current state even if the session was extended by another client (or
  another VS Code window) since this one last had it open.

## Configuration

* `klorb-config.json`: no new keys — session persistence isn't itself configurable in the TUI or
  headless CLI; it always persists a trusted workspace's session at every turn boundary, and
  always auto-restores the most recent one on startup, exactly the same as the input-history
  store's own always-on behavior for a trusted workspace.
* `vscode-plugin`'s `package.json`: `klorb.resumeLatestSession` (boolean, default `true`) — see
  "VS Code plugin" above.

## Out of scope

* Restoring replaces the live `Session`'s config outright with whatever was saved, rather than
  routing it through `klorb.process_config.load_process_config`'s config-layer precedence —
  `_load_saved_session_overrides()` (`klorb.process_config`) is a placeholder reserved for that,
  never wired up. This means a CLI flag like `--model` passed to a fresh invocation is superseded
  by a restored session's own `model`, unlike every config-file layer, where an explicit CLI flag
  always wins. A future change could route just the config portion through that pipeline if flag
  precedence turns out to matter in practice.
* The restored `SessionConfig` is not folded back into `ReplApp._process_config.session` (the
  template a future `/clear` copies from) — a `/clear` right after restoring reverts to whatever
  the process's own startup-time config layers produced, not the restored session's settings.
* No confirmation before restoring, loading, or quitting — every save/restore in this design is
  unconditional whenever the underlying file/lock state allows it, matching how the input-history
  store already auto-attaches with no prompt.
* A headless one-shot run persists its session at the end of the run (`klorb.cli.main()` calls
  `session.close()`) but never auto-restores one — a one-shot invocation always starts from
  whatever config layers/CLI flags produce, gated on the same `TrustManager`/workspace-trust
  machinery as everything else in this feature (see docs/specs/projects-and-trust.md's "Out of
  scope" section, which notes the same limitation for the input-history store).
* A malformed or wrong-schema `session.json`/`sessions.json` is treated as "nothing to
  restore"/"empty index" rather than surfaced as a warning in the history scroll the way a
  `klorb-config.json` parse failure is (`ProcessConfig.config_warnings`) — a possible future
  improvement, not built here.
* Subagent sessions (not yet implemented) will not create their own top-level `sessions/<subdir>/`
  — a subagent's state is expected to be tracked within its root session's own directory, by a
  mechanism this design doesn't define yet.
* No UI action to delete a saved session outright (only the automatic `MAX_RECENT_SESSIONS`
  pruning removes one), and the "Load session"/"Session history" pickers show no recency
  timestamp, only a title — both are possible future improvements, not built here.
* `KlorbAcpAgent.fork_session`/`resume_session` remain unimplemented (`RequestError.
  method_not_found`) — unrelated to `session/load`/`session/list`, and unaffected by this design.
