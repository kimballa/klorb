# Resolve system prompts role-first, then model, then default; `Session` owns the `Role`

* Date: 2026-07-03 19:56
* Question: With system prompts externalized into files
  ([[ship-system-prompts-as-package-data-with-user-config-overrides]]), what are the axes
  and precedence of resolution — and where does the resolved prompt "come from"
  architecturally, given `Model.system_prompt()` used to be its sole source and coding is
  headed toward a multi-agent exercise where several agents may share one model while doing
  different jobs (writing code, exploring, auditing, coordinating)?
* Answer: Role is the primary axis, model the secondary one.
  `Session._resolve_system_prompt()` picks the first non-`None` of:
  `Role.system_prompt(model)` (role+model file, then role default file), then
  `Model.system_prompt()` (top-level model file), then `Session._default_system_prompt()`
  (`default_sys.md`, then the hardcoded `DEFAULT_SYSTEM_PROMPT` constant as a never-in-
  practice safety net). Within every file lookup, the user tier beats the packaged tier;
  across lookups, specificity beats tier (a packaged role-specific prompt outranks a user's
  global `default_sys.md` override). All the intermediate methods return `str | None`, with
  `None` meaning "fall through". Roles are represented by `klorb.role.Role` — abstract, with
  `CoordinatorRole` (the default top-level role) and `NamedRole` (any name without a
  dedicated subclass) — built from the new code-settable-only `SessionConfig.role_name`
  field by `Session.__init__` itself via the `get_role()` factory; callers never pass a
  `Role` object in. `Model.system_prompt()` became a concrete base-class method (it was
  abstract, and every implementation hardcoded the same string).
* Reasoning: A security-audit prompt and a spec-writing prompt on the same weights are
  different *content*, not tuning tweaks — while a model-specific override really is a
  tweak within whatever role is running — so role dominates and model refines. Specificity
  beating tier follows CSS/systemd-drop-in semantics: a user's blanket `default_sys.md`
  override shouldn't silently discard packaged per-role/per-model tuning they never
  touched; overriding a specific behavior requires writing the correspondingly specific
  file. `Session` (not `Model`) owns resolution because only the session knows both axes;
  `Session` constructing its own `Role` from `config.role_name` (rather than accepting a
  `Role` argument) removes the possibility of a session whose `Role` object and
  `role_name` string disagree. `role_name` is deliberately not a `klorb-config.json` key:
  the role is an operational property set by code (the coordinator default today, a
  subagent-spawning call site later), and a project's config file must not be able to
  change what kind of agent the user is talking to. The `str | None` chain keeps test
  fixtures trivial — a fixture model or role overrides one method to return a literal
  string, no filesystem involved. Alternatives rejected: provider as an axis (see the
  packaging ADR); a `SessionContext` object bundling role+config (redundant — the role is
  derivable from `role_name`, and a bundle would reintroduce the disagreement seam); tier
  beating specificity (a one-file user override would clobber all packaged tuning at once).
