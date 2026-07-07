# Reject `trap DEBUG`/`extdebug` as BashTool's classification mechanism

* Date: 2026-07-07 10:05
* Question: Bash's own `trap DEBUG` combined with `shopt -s extdebug` can veto a command before
  it runs, using bash's own real parsing and expansion — unlike an external AST walker, it can
  never disagree with what bash itself is about to do. Why not use it as (or alongside)
  `CommandPermissionsTable`'s classification mechanism instead of parsing with `shfmt`?
* Answer: Rejected as the core mechanism. `klorb.permissions.shell_parse` remains the sole
  classifier, backed by `shfmt --to-json` (see
  [[shell-out-to-shfmt-for-bash-parsing]]), with no `trap DEBUG` involved anywhere in the
  request path.
* Reasoning: `trap DEBUG`/`extdebug` is a *cooperative* mechanism, not an adversarial boundary —
  it relies on the very shell process running the untrusted command to keep honoring a trap that
  same process's own execution could disable. A command string that includes `trap - DEBUG` or
  `set +T` simply turns the veto off before whatever it was meant to gate runs; by default the
  trap doesn't even propagate into `$(...)`, subshells, or shell functions without `set -T`/
  `functrace` also being threaded through everywhere. `BashTool`'s classification has to hold up
  against a command string an untrusted model chose specifically to see what it can get away
  with (the same threat model `CommandRules`' fail-closed-on-anything-not-confidently-classified
  rule exists for — see docs/plans/ready/004-bash-permissions-and-bash-tool.md) — a veto the
  input itself can turn off doesn't meet that bar. `shfmt --to-json` parses the command string
  *before* any shell ever executes a byte of it, entirely outside the process that would need to
  cooperate with a `trap`-based veto, which is why it's the mechanism this plan builds on
  instead.
