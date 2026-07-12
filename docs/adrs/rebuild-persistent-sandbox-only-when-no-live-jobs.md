# Rebuild a stale persistent sandbox only when it has no live background jobs

* Date: 2026-07-12 14:20
* Question: When [[rebuild-persistent-sandbox-when-grants-grow]] determines a persistent shell's
  sandbox is stale and must be replaced, the rebuild `SIGKILL`s the old `bwrap` process tree. A
  `bash` process's state is only partially serializable: cwd and the exported environment are
  cheaply restorable (`export -p` replayed into the new shell), but background jobs (`sleep 30 &`,
  a dev server the model started and left running), disowned/`nohup`'d processes, and open file
  descriptors are *not* — a rebuild kills them along with the old sandbox, with no way around it
  short of the rejected in-namespace-mount approach. Should the harness rebuild transparently
  whenever the allowed-dir set grows, accepting that it may silently kill background work the model
  deliberately started?
* Answer: No — rebuilds are lossless-or-explicit. Before rebuilding, `BashTool._reconcile_sandbox`
  probes the old shell with `jobs -p` (`PersistentShell.has_live_jobs`). If there are **no** live
  jobs, it rebuilds transparently, replays cwd + exported env, runs the command, and sets
  `sandbox_rebuilt=true` on the response so the reconcile is visible in the transcript rather than
  silent. If there **are** live jobs, it does *not* auto-rebuild: the existing shell is left alive
  and untouched, the command does not run, and the response carries a `failure_reason` explaining
  that picking up the new grant requires an explicit `shell_lifetime="new"` — which the model can
  then issue as a deliberate choice, accepting the loss of its background jobs, instead of the
  harness making that call for it.
* Reasoning: The whole reason a persistent shell exists is to carry state across calls; a
  mechanism that silently destroys the most valuable of that state (a running build, a started
  server) to pick up a permission grant would violate the feature's own contract in a way that's
  invisible until the model notices its server is gone. Making the model opt in via
  `shell_lifetime="new"` keeps the destructive choice where the information about whether it
  matters actually is — the model knows whether the background job it started is still needed;
  the harness doesn't. The transparent path is safe precisely because `jobs -p` empty means there
  is no live background work a rebuild could lose (cwd and exported env are both restored), so the
  common build/debug loop that never backgrounds anything gets seamless reconciliation, while the
  rarer "I have a server running" case degrades to an explicit, model-driven respawn rather than a
  silent kill. The residual gap — `jobs -p` cannot see already-`disown`'d processes — is
  acknowledged, not closed: it's the same class of "a shell can detach from its own bookkeeping"
  caveat the backgrounding rule already lives with, and closing it would require process-tree
  inspection the pid namespace deliberately obscures. Choosing `shell_lifetime="new"` as the escape
  hatch costs nothing extra to build: it already means "kill the current shell and start fresh,"
  so it naturally adopts the current (widest-so-far) mount set as a side effect. The rebuild uses
  `SIGKILL` on the old `bwrap` (not the `SIGINT`-first escalation the in-namespace command-timeout
  path uses), since a PID-1-under-a-reaper process can ignore signals it hasn't handled and only
  `SIGKILL` reliably tears the whole sandbox down.
