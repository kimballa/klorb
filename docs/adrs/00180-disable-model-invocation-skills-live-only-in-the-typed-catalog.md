# `disable-model-invocation` skills live only in the typed catalog

* Date: 2026-08-09 00:00
* Question: A skill author may want a skill the model can never autonomously decide to load —
  only the user, by typing `/<name>` as the leading token of their own message, should be able to
  trigger it (e.g. a skill whose instructions are only appropriate when the user explicitly asks
  for that exact behavior). How should such a skill be kept out of the model's own
  `ActivateSkill`/`SearchSkills`/available-skills reach while still working through the
  leading-mention `UserSkillActivation` path, which resolves independently of those?
* Answer: A new `disable-model-invocation: true` `SKILL.md` frontmatter key. A skill carrying it is
  added to `SkillCatalogRegistry.typed()` (so a user's own `/<name>` mention still resolves it,
  through `resolve_reference()`) but never to `canonical()` — the one catalog
  `ActivateSkill`/`ReadSkillFile` resolve against and `discoverable()` enumerates. It's therefore
  automatically absent from the available-skills interjection and `SearchSkills` (both built from
  `canonical().discoverable()`), and `ActivateSkill`/`ReadSkillFile` can never load its content no
  matter how the model learned its name. `resolve_and_gate_skill` still consults `typed()` when a
  `canonical()` lookup misses, purely to give a caller that guessed the name a specific refusal —
  *"it only activates when the user's own message starts with `/<name>`"* — instead of the generic
  "no such skill" an actually-unknown name gets. See docs/specs/skills.md's "Model-invocation-
  disabled skills" section.
* Reasoning: `skillRules` (`deny`/`ask`/`allow`) governs *whether* the model may activate a skill
  it already knows about; it has no way to express *the model must never even consider this skill
  on its own*, since every non-`"deny"` skill is unconditionally listed in the available-skills
  interjection today. Reusing `skillRules` for this (e.g. a skill permanently stuck at `"ask"` with
  no allow path) would still leave it discoverable and nameable, and a sufficiently persistent
  model could still trigger the ask loop repeatedly. Keeping the skill out of `canonical()`
  entirely closes that off structurally rather than by convention: there's no `(namespace, name)`
  pair for `ActivateSkill` to ever successfully resolve, so no policy check can be bypassed or
  misconfigured into granting model access. The tailored refusal message (rather than a bare
  lookup miss) exists because a model that stumbles onto the name some other way (a `SkillReference`
  reminder still fires for a non-leading mention, since that reminder resolves through `typed()`)
  needs a clear, actionable signal — "tell the user to type `/<name>`" — rather than treating it as
  a typo and giving up or hallucinating a workaround.
