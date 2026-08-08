# Model/thinking session config rides `_klorb/setSessionConfig`, not `session/set_model`

* Date: 2026-07-25 20:35
* Question: Plan 016's overview anticipated exposing model choice and thinking enabled/effort
  as "ACP session config options (select + boolean)," with an ext-method fallback "only if the
  pinned SDK version's config-option support proves too immature." The pinned SDK
  (`agent-client-protocol` 0.7.1) turned out to have no generic select/boolean config-option
  surface at all — only one specific native select, `SessionModelState`/`session/set_model`,
  covering `model` alone and marked `**UNSTABLE**` in the SDK's own schema. Should model
  selection use that native (if unstable) surface while thinking rides an ext method, or should
  both ride one ext method uniformly?
* Answer: Both model and thinking enabled/effort ride one uniform ext-method pair,
  `_klorb/getSessionConfig`/`_klorb/setSessionConfig` (see docs/specs/klorb-server.md's "Model
  and thinking session config" section). `session/set_model` stays an explicit
  `RequestError.method_not_found`, the same as every other protocol method this server doesn't
  implement.
* Reasoning: Splitting the two settings onto two different wire mechanisms — model via the
  SDK's native (unstable) select, thinking via an ext method — would mean a client has to drive
  two different request/response shapes to render what is, from a user's perspective, one
  "session config" panel (see plan-016-009's status-row design). That split buys nothing here:
  the native surface doesn't even cover thinking, so an ext method is required regardless, and
  building the native surface *in addition* just to save one field from riding the ext method
  adds a second code path (and a second set of tests) for zero net capability. A single
  ext-method pair with a plain JSON shape is also simpler to keep stable across SDK version
  bumps than a `**UNSTABLE**`-flagged native type that may be renamed or reshaped upstream
  before it stabilizes — the same "moving target" reasoning
  docs/adrs/00160-ask-user-questions-rides-a-klorb-ext-method-not-acp-elicitation.md already applied
  to `AskUserQuestions` vs. ACP elicitation.

  This is a narrower version of the plan's own anticipated fallback, not a rejection of it: the
  plan already named `_klorb/getSessionConfig`/`_klorb/setSessionConfig` (verbatim) as the
  fallback shape to reach for once the native surface proved insufficient in practice — that
  proved true for thinking on day one, so both settings take that path together rather than
  half now, half later. Revisit if a future SDK version adds a real generic config-option
  surface (or promotes `session/set_model` out of `**UNSTABLE**` and adds an equivalent for
  thinking) that would let a single client-side code path drive both by SDK convention instead
  of a klorb-specific one.
