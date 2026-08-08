# Ship the reference `klorb-config.json` as package data, not an uninstalled `etc/` file

* Date: 2026-07-05 04:49
* Question: `klorb init` needs a full reference `klorb-config.json` (every recognized key at
  its default) to copy into `/etc/klorb` or `$KLORB_CONFIG_DIR`. Where should that reference
  file live so `klorb init` can read it from an installed wheel, not just a repo checkout?
* Answer: The reference file lives at `klorb/src/klorb/resources/klorb-config.json`, declared
  under `[tool.setuptools.package-data]` in `klorb/pyproject.toml` alongside the existing
  `system_prompts.d/*.md` entries, and is read at runtime via
  `importlib.resources.files("klorb.resources")` (`klorb.klorb_init.reference_config_text()`)
  — the same package and the same access pattern
  `klorb.system_prompts.resolve_prompt_file()` already uses. It is no longer kept at
  `etc/klorb-config.json` in the repo root.
* Reasoning: `etc/klorb-config.json` predates `klorb init` (see the archived
  `docs/plans/archive/001-klorb-init-cmd.md`) and was deliberately left uninstalled because
  neither `pip install` nor `uv install` reliably supports writing to absolute host paths like
  `/etc/klorb` or `~/.config/klorb` at install time. That constraint is about install-time
  placement into *host* paths, not about whether the file ships inside the wheel at all — the
  same reasoning already applied to system prompts in
  [[ship-system-prompts-as-package-data-with-user-config-overrides]]. Once `klorb init`
  exists as the thing that actually performs that host-path placement (on demand, not at
  install time), the reference file it copies from only needs to exist *inside the installed
  package*, which `package-data` + `importlib.resources` already handles reliably. Leaving it
  at the repo-root `etc/` path would mean `klorb init` silently does nothing (or errors) for
  anyone using a real `pip`/`uv` install rather than a repo checkout, since `etc/` isn't part
  of the wheel; moving it under `klorb/src/klorb/resources/` fixes that with no new
  packaging mechanism, and keeps exactly one reference copy on disk (the repo-root file was
  deleted, not duplicated) per the project's no-duplicated-constants rule.
