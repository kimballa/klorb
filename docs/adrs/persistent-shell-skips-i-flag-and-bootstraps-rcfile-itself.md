# A session-scoped persistent shell launches plain `bash` (no `-i`/`--rcfile`) and sources `~/.bashrc` itself as its first command

* Date: 2026-07-08 19:00
* Question: The one-shot `Bash` path (`shell_lifetime="command"`) launches
  `bash --rcfile ${HOME}/.bashrc -i -c "<command>"` — `-i` is what makes bash source `~/.bashrc`
  despite being non-login (see
  docs/adrs/bash-env-uses-clearenv-plus-shareenv-setenv-plus-forced-rcfile.md). Should
  `PersistentShell` (`klorb.tools.bash`, backing `shell_lifetime="session"`/`"new"`) launch with
  the same `--rcfile ${HOME}/.bashrc -i` flags, just without the one-shot `-c "<command>"`, and
  feed it commands over its stdin pipe instead?
* Answer: No. `PersistentShell` launches plain `bash` (no `-i`, no `--rcfile`) and, as its very
  first command (run through the ordinary sentinel-delimited `run_command` channel, in
  `BashTool._spawn_persistent_shell`), runs `PS1=x\n[ -f "<rcfile>" ] && source "<rcfile>"\nunset
  PS1 PS2\n` — setting a dummy `$PS1` just long enough to satisfy `~/.bashrc`'s own `[ -z "$PS1"
  ] && return` guard, sourcing the file directly, then unsetting `PS1`/`PS2` again.
* Reasoning: Confirmed empirically against this project's own dev/cloud environment. The one-shot
  path only ever runs *one* command via `-c`, so `-i` never puts bash into its interactive
  read-eval-print loop over stdin — the only user-visible cost is two fixed, stripped startup
  noise lines (`bash: cannot set terminal process group...`, `bash: no job control in this
  shell`). `PersistentShell` is fundamentally different: it never exits between commands, so it
  *is* stdin-fed one line at a time, which means `-i` really does put it into the interactive
  read loop. Direct testing showed that loop writes a `PS1` prompt to stderr before every read —
  even with a `PS1='x'`-then-`unset` bootstrap sent as the shell's first input, the very first
  prompt (built from whatever `PS1` `~/.bashrc` set) is emitted *before* bash has read and
  processed that unset, and, worse, that prompt text was observed interleaved/garbled with the
  bootstrap script's own literal text arriving on the same stdin — corrupting the
  sentinel-delimited stderr stream this shell's whole command-boundary-detection scheme depends
  on. A plain, non-interactive `bash` never enters that read-eval-print loop at all and never
  prints a prompt, so nothing needs to be stripped from its stderr the way
  `_strip_bash_shell_noise` strips the one-shot path's fixed lines — this class of noise simply
  doesn't exist for `PersistentShell`.

  This has one further, initially-unplanned but ultimately favorable consequence, confirmed by
  the same empirical testing: `--rcfile`/`-i` requires the shell to be interactive to take effect
  at all, and an *interactive* bash traps `SIGINT` for itself (only its running foreground child
  dies on `Ctrl-C`; the shell survives to its next prompt) — which, applied to
  `PersistentShell`'s own timeout-escalation `SIGINT`-then-`SIGKILL` design (see
  docs/adrs/sentinel-tokens-not-a-pty-delimit-persistent-shell-commands.md), would have let a
  plain, non-trapping timed-out command (e.g. `sleep 5`) *recover* rather than end the shell —
  contradicting docs/plans/archive/005-session-scoped-bash-terminals.md's own stated test
  expectation that a timed-out `sleep` reports `terminal_alive=false`. A *non*-interactive bash's
  default `SIGINT` disposition is to terminate (it does not trap and survive it), so
  `os.killpg(..., SIGINT)` on timeout ends both the stuck foreground command *and* the shell
  itself for the common case, without needing to wait out the full `SIGKILL`-escalation grace
  period — verified directly: a plain `sleep 5` under a 1-second timeout returns in ~1.0s with
  `terminal_alive=False`, while a command that explicitly makes itself immune (`trap '' INT;
  sleep 5`) correctly consumes the full grace period before the `SIGKILL` escalation kicks in at
  ~4.0s. Both outcomes match the plan's intent; neither would have held with `-i`.
