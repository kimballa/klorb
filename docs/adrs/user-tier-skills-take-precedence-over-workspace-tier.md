# User (homedir) skills take precedence over workspace (project) skills, which take precedence over internal

* Date: 2026-07-19 00:00
* Question: docs/specs/skills.md originally shadowed a same-named skill across tiers in the order
  `workspace`, `user`, `internal` — a project-supplied skill always won over a user's own homedir
  skill of the same name. Should a workspace-tier skill continue to override a same-named
  user-tier one, or should the precedence be reversed?
* Answer: Reversed. `klorb.permissions.skill_access.VALID_NAMESPACES` is now `("user",
  "workspace", "internal")`, and every place that iterates tiers to decide which one wins a
  same-named collision (`klorb.tools.skill.common._tier_source_dirs`/`resolve_all_skills`, and the
  catalog's `SkillCatalog.precedence_deduped()`) iterates in that order. A skill in
  `$KLORB_DATA_DIR/skills/foo/` now shadows a same-named `${workspace_root}/.klorb/skills/foo/`,
  which in turn shadows a same-named packaged `internal` skill.

  This only changes which tier *a bare, unqualified `/foo` mention means* when more than one tier
  has that name. It does not change permission identity: `(namespace, name)` stays the axis
  `skillRules` and grants are keyed on, so a lower-precedence tier's copy is still independently
  addressable by its exact pair (`ActivateSkill(namespace="workspace", name="foo")`, or a typed
  `/workspace:foo` reference) and carries its own verdict, never inheriting one made for a
  different namespace's same name.
* Reasoning: A user's own homedir skill is a personal customization they installed deliberately,
  across every project they work in. A workspace-tier skill is something a project's repository
  shipped — which could be an unfamiliar or even hostile-but-trusted checkout the user just started
  working in. Letting a freshly-cloned repository's own skill silently shadow a name the user has
  been relying on in their `$KLORB_DATA_DIR/skills/` for other projects is the wrong default: the
  user's own accumulated tooling should win, exactly the same trust ordering `readDirs`/`writeDirs`
  already give a user-level config layer over a project-level one for the same reason. The
  packaged `internal` tier stays last regardless, since it's klorb's own built-in fallback, never a
  customization a user or project actively chose to add.
