# Bubblewrap is mandatory defense-in-depth, not an alternative to command classification

* Date: 2026-07-07 10:10
* Question: `BashTool` has two independent safety layers — `CommandRules`/`readDirs`/`writeDirs`
  classification (`klorb.permissions.command_access`/`klorb.permissions.shell_parse`) and an
  OS-level `bwrap` sandbox boundary (`klorb.sandbox`, argv construction still a stub — see
  docs/plans/ready/004-bash-permissions-and-bash-tool.md). Given classification already fails
  closed on anything it can't confidently parse, is a second, much more expensive-to-build
  sandbox layer actually load-bearing, or would getting classification right be sufficient on
  its own?
* Answer: Both layers are required; neither is optional once `bwrap` argv construction lands.
  `evaluate()`/`raise_if_not_allowed()` gate whether a command *runs at all*; the (future) `bwrap`
  boundary bounds what an *approved* command can actually reach on the filesystem/network/process
  table once it's running, independent of whether the classifier understood it correctly.
* Reasoning: `klorb.permissions.shell_parse`'s classifier is a best-effort, syntax-level read of
  the command string — it can only reason about what's *visible in the AST*. It cannot see what
  an approved command's own `open()`/`write()`/`connect()` calls do once it's actually running: a
  `python -c "..."` one-liner, a compiled binary, or any interpreter the walker can't see inside
  of can do arbitrary I/O the classifier has no visibility into, even when every token it *can*
  see was a literal and matched an `allow` rule in good faith. This is exactly the class of gap
  goal #0 in the plan's "Context" section describes ("the agent is at all times permitted the
  least possible access privileges... necessary to accomplish its approved goal") — a classifier
  alone only bounds *which commands run*, not *what a running command can touch*. `bwrap`'s
  mount/network/pid namespace boundary works below the shell/command layer entirely, so it
  doesn't need to understand bash syntax (or any language's syntax) at all to constrain a
  misclassified or unexpectedly-behaving command's blast radius. The two layers answer different
  questions ("should this run" vs. "what can it reach while running") and neither can substitute
  for the other; the "Worked example" in the plan doc (`curl ... | sh`) illustrates the
  intersection: even if a permissive config auto-approved that pipeline, the sandbox layer still
  bounds the outcome to whatever `workspace_root`/granted write dirs and (denied-by-default)
  network access the session was actually granted.

  Because `bwrap` argv construction can't be developed or verified in this project's own
  dev/cloud-agent environments (`bwrap_available()` reports `False` there — unprivileged user
  namespaces aren't permitted in a nested container; see `klorb.sandbox`'s module docstring),
  `BashTool` runs unsandboxed in those environments, with a one-time per-session notice
  (`klorb.tools.bash._sandbox_notice`) making that reduction in guarantee explicit rather than
  silent.