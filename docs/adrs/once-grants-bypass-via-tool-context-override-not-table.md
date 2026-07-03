# "Allow once" bypasses via a one-shot `ToolSetupContext.permission_override`, not a table mutation

* Date: 2026-07-03 18:20
* Question: "Allow (once)" must unblock exactly the one tool call currently pending — and nothing
  else, ever again — with zero persisted state, not even the in-memory `SessionConfig.read_dirs`/
  `write_dirs` the other five choices all mutate. Every other Allow choice works by adding an
  `allow` entry (and removing the matched `ask` entry) so the *tables themselves* legitimately
  evaluate to `"allow"` on a retried call. Since "once" must add nothing anywhere, how does the
  retried call actually succeed?
* Answer: A new `ToolSetupContext.permission_override: Path | None` field. `evaluate_write()`/
  `resolve_and_evaluate_read()` (`klorb.permissions.workspace`) check it — after the
  unconditional `is_privileged_path()` deny (never bypassable), before consulting either table —
  and short-circuit to `"allow"` if it's set and equals the path being evaluated exactly.
  `ToolRegistry.instantiate_tool()` gained a matching optional `permission_override` parameter,
  threaded into the fresh `ToolSetupContext` it builds for that one call.
  `Session._retry_after_permission_decision()` retries via
  `tool_registry.instantiate_tool(call.name, permission_override=ask_exc.path)` — a brand-new
  `Tool`/`ToolSetupContext` pair, discarded immediately after that single `apply()` call, per
  `ToolRegistry`'s existing "fresh instance per call" contract (see
  [the fresh-instance ADR](tool-registry-instantiates-a-fresh-tool-per-call.md)). No `DirRules`
  list is ever touched.
* Reasoning: `ToolRegistry` already constructs a brand-new `Tool` and `ToolSetupContext` for
  every single tool call, specifically so nothing carries state between calls — that existing
  seam is exactly the right place to inject a value that must live for precisely one call and no
  longer. Reusing the `readDirs`/`writeDirs` tables for a one-shot grant would have required
  either a genuinely temporary rule (added before the retry, then removed again immediately
  after — extra bookkeeping and a real risk of leaking the rule if the retry itself throws before
  cleanup runs) or a separate "ephemeral allow" concept layered onto `DirRules`, complicating a
  model (`PermissionsTable`/`DirRules`) that's deliberately simple and, per its own documented
  contract, never mutated in place. A field on `ToolSetupContext` instead piggybacks on
  machinery that already exists for an unrelated reason (per-call freshness) and disappears
  automatically the moment that one `ToolSetupContext` goes out of scope — there is no cleanup
  step to forget.

  This mechanism is only sound because every tool this pass covers (`ReadFile`, `EditFile`,
  `ReplaceAll`, `CreateFile`) resolves exactly one path exactly once per `apply()` call. A
  hypothetical future tool that touches multiple paths in one call (a batch rename, say) would
  need either several overrides or a different one-shot-bypass design entirely — not a blocker
  today, but worth remembering before assuming this pattern generalizes to every future tool
  without changes.
