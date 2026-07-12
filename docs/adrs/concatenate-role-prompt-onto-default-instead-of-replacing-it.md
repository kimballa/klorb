# Concatenate the role prompt onto the default prompt instead of the role replacing it

* Date: 2026-07-11 00:00
* Question: [[resolve-system-prompts-role-first-then-model-then-default]] made role the
  primary resolution axis by picking the first non-`None` prompt across role, then model,
  then `default_sys.md` — so a role that resolves any prompt file (today, every coordinator
  session) never sees `default_sys.md` at all, and `roles/coordinator/default.md` has to
  hand-repeat `default_sys.md`'s general engineering discipline (grounding, minimal diffs,
  verification, honest reporting) to keep the coordinator seeing it. As more roles
  (`ExploreRole`, `AuditorRole`, ...) get their own prompt files, should each keep repeating
  that same material, or should the resolver guarantee every session sees it?
* Answer: `Session._resolve_system_prompt()` runs two independent resolver walks instead of
  one shared chain, and concatenates their results rather than the first hit winning
  outright. The **default walk** (`Model.system_prompt()`, then `default_sys.md`, then the
  hardcoded `DEFAULT_SYSTEM_PROMPT` constant) is role-agnostic and always produces a prompt.
  The **role walk** (`Role.system_prompt(model)`: role+model file, then role default file)
  may produce `None`. The default walk's result is always the base of the final prompt; when
  the role walk also produces one, it's wrapped in an `<AgentRole>...</AgentRole>` tag and
  appended after the default prompt. A role can no longer opt out of the default prompt —
  only add to it.
* Reasoning: A role prompt is naturally an *addendum* — "and also, for this job, do X" — not
  a wholesale replacement of the baseline engineering discipline every klorb agent should
  follow regardless of what job it's doing. Making the resolver guarantee `default_sys.md`'s
  presence means each role file only has to state what's distinctive about that role, instead
  of every role file re-deriving (and risking drifting out of sync with) the same shared
  material — a concern that only grows as more roles are added for the multi-agent work this
  file tree exists to serve. Wrapping the role prompt in `<AgentRole>` (rather than just
  concatenating raw text) gives the model an explicit signal for where the role-specific
  layer starts, mirroring how `_wrap_system_interjection` already tags out-of-band harness
  content elsewhere in the same file. Alternatives rejected: keep first-hit-wins and require
  every role file to repeat the default's material by convention (already showed signs of
  drift, and doesn't scale to more roles); make the default walk itself role-aware so a role
  could suppress it (defeats the point — a role that could opt out of baseline discipline
  isn't a constraint worth having); concatenate without a wrapper tag (loses the explicit
  boundary the `<SystemInterjection>` convention elsewhere in the prompt already establishes,
  and makes it harder for a model asked to describe its own instructions to distinguish
  universal rules from role-specific ones).
