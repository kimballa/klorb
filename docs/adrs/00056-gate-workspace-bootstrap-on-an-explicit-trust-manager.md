# Gate all workspace-trust behavior in `ReplApp` on an explicit, optional `TrustManager`

* Date: 2026-07-05 06:00
* Question: `ReplApp` now resolves and (if needed) interactively bootstraps workspace trust at
  startup, announces the result in the history, and offers a "Trust workspace" palette command
  (see docs/specs/projects-and-trust.md). Every existing test in `test_tui_repl.py` — dozens of
  them — constructs `ReplApp(session=...)` directly, with no notion of a workspace or trust
  manager at all, and none of them expect a startup modal to appear. Should workspace-trust
  resolution be unconditional (every `ReplApp` gets it, using some default `TrustManager` if none
  is given), or should it be opt-in?
* Answer: Opt-in, gated on a new `ReplApp.__init__(trust_manager: TrustManager | None = None)`
  constructor argument. When `None` (the default — and every pre-existing test's constructor
  call, unchanged), `_resolve_workspace_trust()` is a no-op: no startup modal, no history
  announcement, no "Trust workspace" palette command (`workspace_trust_management_enabled()`
  returns `False`). Only `klorb.cli.main()`'s real, interactive invocation constructs a
  `TrustManager` and passes it in.
* Reasoning: A default `TrustManager()` would resolve to `$KLORB_DATA_DIR/projects.json` — a
  real file under the developer's own home directory — so any test that didn't explicitly
  isolate it would risk reading or writing that file, or hanging on a startup modal nobody
  answers. Requiring every one of the dozens of pre-existing tests to thread through an isolated
  `TrustManager` (or monkeypatch `KLORB_DATA_DIR`) just to keep constructing `ReplApp` the way
  they already do would be a large, purely mechanical diff across a file that has nothing to do
  with this feature — exactly the kind of unrelated churn CLAUDE.md says a change shouldn't
  carry. Making the whole feature opt-in via a constructor argument keeps every existing test
  passing unmodified, and keeps the workspace-trust tests that do care
  (`test_tui_repl.py`'s "workspace trust" section) explicit about the `TrustManager` (and its
  isolated `tmp_path`-scoped file) they're using. This mirrors `session_log_enabled`/
  `initial_message` — `ReplApp` already has other constructor flags that opt a real CLI-launched
  REPL into behavior a test-constructed one doesn't want by default.
