# Every child process klorb shells out to runs in its own session (`start_new_session=True`)

* Date: 2026-07-08 18:00
* Question: `docs/adrs/bash-env-uses-clearenv-plus-shareenv-setenv-plus-forced-rcfile.md` argues
  `BashTool`'s `-i` costs nothing beyond two fixed, stripped stderr lines because the child has
  "no controlling tty (stdin from `/dev/null`, per this tool's design)". That claim was verified
  against `stdin`/`stdout`/`stderr` all redirected away from any tty — but none of that actually
  detaches the child from klorb's own *session*. When klorb itself runs attached to a real
  terminal (an ordinary interactive use, not this project's own sandboxed dev/CI environment),
  does a spawned shell child staying in that same session create a real risk of it contending
  with klorb for the terminal's foreground process group — and if so, should every place klorb
  shells out to a real shell (`BashTool._execute()` for model-issued commands,
  `UserShellCommand.run()` for the REPL's `!`-prefixed user commands) do something about it
  beyond redirecting the standard fds?
* Answer: Yes, in both places. Both `subprocess.Popen(...)` calls now pass
  `start_new_session=True`, which calls `setsid()` in the child before `exec()`. This puts the
  child in a brand-new session with no controlling terminal at all — not contingent on what its
  own `stdin`/`stdout`/`stderr` happen to be connected to, and not contingent on klorb's own
  terminal situation either. It also makes the child its own process group leader (`pgid ==
  pid`), which both call sites' kill paths now rely on: a timeout (both tools) or cancellation
  (`UserShellCommand`) kills the whole group via `os.killpg(process.pid, signal.SIGKILL)` rather
  than `process.kill()` on the single pid. `UserShellCommand.run()` additionally now passes
  `stdin=subprocess.DEVNULL` (`BashTool` already did) — its `Popen` call previously had no
  `stdin=` override at all, so the child inherited klorb's own stdin, which in an interactive TUI
  session is the real controlling terminal; nothing about `UserShellCommand`'s design (piped,
  line-buffered `stdout`/`stderr` captured into the TUI history) ever depended on the child
  reading the user's live keystrokes.
* Reasoning: `BashTool` sources the user's real `~/.bashrc` (via `-i --rcfile`) to recompute
  PATH/toolchain setup, and `UserShellCommand` runs an arbitrary shell command a user typed
  directly — in both cases, code not written or audited with "runs headlessly under klorb" in
  mind. Plenty of ordinary shell setup assumes it's safe to touch the controlling terminal:
  starting an `ssh-agent`/`gpg-agent`/`keychain` bootstrap job in the background, a prompt
  framework's first-run setup, anything that opens `/dev/tty` by name rather than relying on
  `stdin`. None of that is reachable from a `BashTool` child today (bash's own `-i` job-control
  init checks `stdin`/`stderr` for a tty and correctly stays inert when both are redirected, per
  the empirical testing behind the earlier ADR) — but relying on "nothing downstream happens to
  go looking for the real terminal" is a fragile, un-enforced assumption, not a guarantee, and it
  was only ever verified in an environment (this project's own sandboxed dev/CI setup) that
  doesn't exercise the interactive-terminal case at all. `setsid()` turns "probably fine because
  nothing currently reaches for the terminal" into "structurally impossible to reach the terminal
  at all" (`/dev/tty` opens fail with `ENXIO` in a session with no controlling terminal), closing
  the whole class of interaction rather than one specific trigger.

  `UserShellCommand`'s case is the more directly exposed of the two, not just the more
  precautionary one: before this change its child's `stdin` genuinely *was* the live controlling
  terminal (inherited, no override), and klorb's own TUI is actively depending on holding that
  same terminal to render itself — so a child (or a background job it launches) contending for
  the terminal's foreground process group risks disrupting klorb's own rendering, not just some
  hypothetical downstream misbehavior.

  Killing the process group instead of the single pid on timeout/cancellation is a related,
  independent fix both changes happen to unlock cheaply. For `BashTool`, `_decode_exit()`'s own
  docstring already documents that bash's tail-call-exec optimization means `process.pid`
  sometimes *is* the target command and sometimes is a `bash` that forked a distinct child for
  it — either way, a background job something in the chain started (most plausibly from a
  sourced `~/.bashrc`) is a different process from `process.pid` and would previously survive a
  timeout as an orphan. For `UserShellCommand`, the same applies to any background job the
  user's own typed command starts. `os.killpg` reaches everything in the new group in one call,
  with no separate process-tree walk needed, because `start_new_session=True` guarantees the
  group has exactly the members this invocation created.

  This has no effect on either tool's documented model-/user-facing behavior. `BashTool`'s `Bash`
  call is already documented as spawning "its own fresh, non-persistent shell" for
  `shell_lifetime="command"` (see docs/specs/bash-tool-and-command-permissions.md's "Execution"
  section) — nothing about that contract implied sharing klorb's own session. `UserShellCommand`
  was already designed around captured, piped output rather than handing the child real
  interactive terminal control, so it was never suited to genuinely full-screen interactive
  programs (`vim`, `htop`) in the first place, independent of this change — those wouldn't render
  correctly through a captured, line-buffered pipe regardless of `stdin`/session handling. No
  test in either tool's suite depended on the previous, session-sharing behavior.
