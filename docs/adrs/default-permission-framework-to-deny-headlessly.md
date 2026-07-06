# Default permissionFramework to "ask" interactively, "deny" headlessly

* Date: 2026-07-06 00:00
* Question: A headless one-shot run (`klorb -m ...`) already fails closed on any tool call
  that hits a `readDirs`/`writeDirs` `"ask"` permission verdict — there's no modal to show,
  and `klorb.cli.main()` never passes an `on_permission_ask` callback — but that behavior
  was an implicit fallback, not a documented or configurable policy. Should headless runs
  keep failing closed by default, or auto-approve by default so an unattended run doesn't
  stall on an ask it can't answer? And should there be a way to opt into auto-approval
  without hand-editing a config file?
* Answer: Add `SessionConfig.permission_framework: Literal["ask", "auto", "deny"]`,
  defaulting to `"ask"`. `klorb.cli.main()` resolves the effective value explicitly (it's
  deliberately excluded from `klorb.process_config.SESSION_KEY_MAP`, the same way
  `interactive` is): `"deny"` when the session is non-interactive, `"ask"` (the
  `SessionConfig` default) when interactive, and `"auto"` whenever `-y`/`--auto-approve` is
  passed, regardless of interactivity. `Session._run_tool_calls()` checks
  `permission_framework` before ever consulting an `on_permission_ask` callback: `"deny"`
  fails closed unconditionally; `"auto"` auto-approves via a synthesized
  `PermissionDecision(choice="session")`, reusing the existing "Allow (this session)" grant
  path (`Session._retry_after_permission_decision`/`klorb.permissions.grant.apply_permission_grant`)
  so nothing is ever persisted to disk; `"ask"` keeps today's behavior (interactive modal
  if a callback is given, else fail closed). The interactive REPL's status row shows the
  session's current `permission_framework` value as a small badge, so a user launched with
  `-y` always sees that asks are being auto-approved.
* Reasoning: Failing closed by default for headless runs is the safe choice — an unattended
  script should never silently gain filesystem access it wasn't explicitly granted, and
  today's implicit fallback already behaved this way, so making `"deny"` the explicit
  headless default changes nothing observable. Auto-approval needs to be an explicit,
  visible opt-in (`-y`/`--auto-approve`) rather than a new implicit default, both because
  it's a meaningfully more permissive posture and because a silent default change would be
  a surprising regression for existing headless callers. Reusing the `"session"` grant
  scope for `"auto"` (rather than inventing a fourth, harness-only grant path) keeps the
  auto-approval in-memory-only and time-bounded to the current process, exactly matching
  the blast radius a `-y` user should expect — it never touches `klorb-config.json`, unlike
  the `"workspace"`/`"homedir"` scopes a real interactive user might choose. Checking
  `permission_framework` ahead of the callback (rather than only changing what
  `klorb.cli.main()` passes as a callback) keeps the policy in `Session` — library code —
  rather than duplicating "auto-approve" logic in both `cli.py` and a hypothetical future
  VSCode-plugin call site, per this repo's CLI/library firewall.
