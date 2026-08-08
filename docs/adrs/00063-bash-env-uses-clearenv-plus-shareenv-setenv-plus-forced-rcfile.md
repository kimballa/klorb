# BashTool's environment: explicit allowlist + forced `.bashrc` sourcing via `-i --rcfile`

* Date: 2026-07-07 10:20
* Question: `BashTool` must not silently hand a model-requested command the klorb process's
  entire environment (secrets, unrelated tokens, ambient state) — but requiring the user to
  manually enumerate every toolchain-related variable (`NVM_DIR`, `PYENV_ROOT`, `CARGO_HOME`, ...)
  in config is cumbersome, since that setup logic already lives in the user's own `~/.bashrc`.
  How should `BashTool` build the command's environment without either leaking everything or
  making ordinary toolchain-dependent commands (`npm`, `pyenv`-installed `python`, etc.) fail?
* Answer: `klorb.tools.bash.build_bash_env` starts from nothing (no implicit inheritance —
  `subprocess.Popen(..., env=...)` always receives an explicit dict, never `None`), then layers:
  `HOME`/`USER` always (auto-shared, matching the plan's "pass-thru" section), then every name in
  `SessionConfig.share_env` (on-disk `shareEnv`, concatenated across config layers) that's
  actually set in the klorb process's own environment, then `SessionConfig.set_env` (on-disk
  `setEnv`, merged key-by-key across layers, later layers winning) as overrides applied last.
  Separately, the command is run via `bash --rcfile ${HOME}/.bashrc -i -c "unset PS1; unset PS2;
  <command>"` (no `--login`) so `~/.bashrc` still gets sourced — recomputing PATH/toolchain setup
  the same way the user's own interactive shell already does — without requiring every one of
  those variables to be hand-enumerated in `shareEnv`.
* Reasoning: This is a real, verified trade-off (empirically tested against this repo's own dev
  environment, not assumed) between "cumbersome to configure" and "silent no-op for most real
  `.bashrc` files":
  * A plain `bash -c` never reads `~/.bashrc` at all (non-interactive, non-login).
  * Pointing `BASH_ENV` at `~/.bashrc` silently does nothing for the overwhelming majority of
    real `.bashrc` files, because the standard Debian/Ubuntu skeleton (and countless files
    copying its idiom) begins with `[ -z "$PS1" ] && return` — and `$PS1` is never set for a
    `bash -c` invocation, `BASH_ENV` or not.
  * `bash --rcfile ~/.bashrc -i -c "<command>"` **does** work: `-i` causes bash to set a
    non-empty default `$PS1` before running rc files, satisfying that guard.
  * `-i` with no controlling tty (stdin from `/dev/null`, per this tool's design) costs exactly
    two fixed, deterministic bash-internal stderr lines on every invocation (`bash: cannot set
    terminal process group...`/`bash: no job control in this shell`), stripped via
    `klorb.tools.bash._strip_bash_shell_noise` before the model ever sees them — an exact
    prefix/suffix match on those two known messages (the process-group line's parenthesized
    number varies by environment, observed as both `-1` and a real pid), not a general regex
    classifier, so it doesn't conflict with this plan's "no regexp-based classification" rule —
    it's filtering known harness-induced noise, not judging a command's safety.
  * `-i` making bash "interactive" only affects bash's *own* behavior (job control, alias/history
    expansion, reading rc files) — it does not fake whether *child processes* see a real
    controlling terminal; a child's `isatty()` (the check well-behaved tools actually use to
    decide whether to prompt/color/launch an editor) correctly reports `False` regardless of
    `-i`, identical to the non-`-i` case. `unset PS1; unset PS2;` is prepended to the actual
    command string, after rc-file sourcing but before the model's command runs, so `$PS1`
    doesn't leak into the target command's own child processes' environment either, closing off
    the one narrower wrinkle (some nonstandard tool checking "is `$PS1` set" instead of
    `isatty()`) even though it's a cheap, non-load-bearing precaution.

  The explicit-dict-`env=` requirement matters specifically because there's no `bwrap --clearenv`
  to lean on while sandboxing is unimplemented (see
  [[bubblewrap-is-defense-in-depth-not-a-classifier-substitute]]): `subprocess.Popen` inherits the
  *entire* parent environment by default when `env=` is omitted, which would silently defeat the
  least-privilege intent in exactly the path where there's no kernel boundary left to fall back
  on. Building the dict explicitly, every time, closes that gap regardless of sandbox
  availability.
