# Use `:` (not `/`) to separate a skill's namespace from its name in a fully-qualified skill name

* Date: 2026-07-19 00:00
* Question: A skill's fully-qualified name (fqsn) — used in `skillRules` config entries
  (`sessionDefaults.skillRules.{deny,ask,allow}`) and, newly, in a user-typed disambiguating
  prompt reference — originally used `/` as the `<namespace>/<name>` separator (e.g.
  `"internal/create-edit-skill"`). Now that a user can also type `/<namespace>:<name>` in a
  prompt to reference one specific skill unambiguously (e.g. `/internal:my-skill`), should the
  fqsn separator stay `/`, or become something else?
* Answer: `:`. `klorb.permissions.skill_access.format_fqsn`/`parse_fqsn` now produce/parse
  `"<namespace>:<name>"`; every on-disk `skillRules` entry, `klorb.resources/default-config.json`'s
  pre-populated `"internal:create-edit-skill"` allow entry, and `klorb.process_config`'s
  layer-concatenation parsing all moved to the colon form. `is_valid_skill_name` now also rejects
  `:` in a skill name outright, so the separator can never collide with name content.
* Reasoning: `/` is already the character that introduces a skill mention in prompt text (`/foo`).
  Once a user can disambiguate which tier they mean by typing `/internal:my-skill`, a second `/`
  inside that reference (`/internal/my-skill`) reads exactly like a filesystem path or a
  division/fraction, and is genuinely ambiguous with those other things `/`-prefixed text already
  means elsewhere in a klorb prompt — there is no clean way to tell "a path" from "a
  namespace-qualified skill mention" if both use `/` as an internal separator too. A colon has no
  competing meaning in this position and reads naturally as a qualifier (similar to
  `namespace:name` conventions elsewhere, e.g. Kubernetes context names or Docker image tags).
  Banning `:` from skill names outright (rather than just escaping it) keeps parsing trivial —
  split on the first `:`, no escaping rules to get wrong — mirroring the existing "a skill name has
  no path separator" invariant.
