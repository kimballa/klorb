# Persistent-shell command boundaries are detected via a printed sentinel token, not a pty

* Date: 2026-07-08 19:00
* Question: `PersistentShell` (`klorb.tools.bash`, backing `shell_lifetime="session"`/`"new"`)
  never exits between commands, so there's no process-exit signal to wait on for "this command is
  done." How should it detect a command's completion and recover exit status, and how should a
  hung command's timeout be enforced without a pty or job-control layer?
* Answer: Each command is followed, in the same script written to the shell's stdin, by
  `__klorb_ec=$?` (captured immediately, before anything else can clobber `$?`) and two `printf`
  statements — one to stdout, one to stderr — each emitting a line matching
  `__KLORB_DONE_<token>__[ <exit_code>]`, where `<token>` is a fresh `uuid4().hex` generated fresh
  per call (never a fixed string). Two background reader threads (`_pump_lines`, one per stream)
  relay every line onto a shared queue; `PersistentShell._run_raw` consumes that queue until it
  sees both streams' matching sentinel lines, collecting everything before them as the command's
  output. On timeout, `SIGINT` is sent to the shell's whole process group first, with a
  `_TIMEOUT_GRACE_SECONDS` grace period for the sentinel to still appear before escalating to
  `SIGKILL` on the process group — which ends the persistent shell itself, not just the stuck
  command, since there is no way to kill only the stuck command without a pty/job-control layer.
* Reasoning: A real pty (via Python's `pty` module or a `pexpect`-style dependency) plus OSC 133
  shell-integration escape sequences (the prompt-boundary marker convention VS Code/iTerm2/kitty
  use) would be a more robust way to delimit command boundaries, and would also make interactive
  programs viable through this channel. It's a materially bigger lift — a new dependency or
  nontrivial pty plumbing, plus a `PROMPT_COMMAND`/`trap DEBUG` hook to emit the escape codes.
  Note that docs/plans/ready/004-bash-permissions-and-bash-tool.md's rejection of `trap DEBUG` as
  a *security* boundary (docs/adrs/reject-trap-debug-as-a-security-boundary.md) doesn't apply
  here: that ADR is about using `trap DEBUG` to veto an *adversarial* command before it runs; this
  would be purely cooperative scripting the harness applies to its own terminal feature, a
  different threat model entirely. Still, it's deferred as documented future work rather than
  built now — the sentinel-token approach is simpler, needs no new dependency, and is sufficient
  for the "no controlling terminal" scope this tool already operates under (matching the one-shot
  path's own no-pty design; interactive programs that need a real terminal don't work through
  either path).

  A random-per-call token (rather than a fixed sentinel string) avoids collision with a command's
  own real output — the same class of concern docs/specs/bash-tool-and-command-permissions.md
  already flags for other constructs, applied here to output framing instead of input
  classification. Sending `SIGINT` before `SIGKILL` on timeout is a deliberate two-stage
  escalation, not just "kill it faster": paired with launching the persistent shell
  non-interactively (docs/adrs/persistent-shell-skips-i-flag-and-bootstraps-rcfile-itself.md), a
  non-trapping stuck command's own process (and the shell process itself, which does not survive
  `SIGINT` when non-interactive) both end promptly on the first signal in the common case; only a
  command that goes out of its way to ignore or block `SIGINT` needs the full grace period before
  the unconditional `SIGKILL` escalation. Each `printf`'s leading `\n` guarantees the sentinel
  always lands on its own line even when the command's own last line had no trailing newline of
  its own — `_run_raw` strips exactly that one synthetic character back off the end of whichever
  stream actually reached its sentinel, so a captured stream matches what the command actually
  wrote rather than what this protocol added on top of it.
