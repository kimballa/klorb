# Split the packaged config resource into a merged `default-config.json` and a copied `template-config.json`

* Date: 2026-07-05 05:17
* Question: The single packaged `klorb.resources/klorb-config.json` (added when `klorb init`
  was built — see [[ship-reference-klorb-config-as-package-data]]) served two jobs at once:
  it was the reference file `klorb init` copies to `/etc/klorb` or `$KLORB_CONFIG_DIR`, and it
  was meant to enumerate every recognized key at its shipped default. Should one packaged file
  keep serving both jobs, or should they split into two?
* Answer: Split into two packaged files, both under `klorb/src/klorb/resources/`:
  `default-config.json`, which `klorb.process_config.load_process_config()` reads directly
  (via `_default_config_layer()`) and merges as the very first, lowest-precedence layer on
  every process start — see [[process-and-session-config]] — and `template-config.json`, a
  deliberately spartan starter file that only `klorb init` ever reads, copying it to
  `/etc/klorb/klorb-config.json` or `$KLORB_CONFIG_DIR/klorb-config.json` for a user to
  hand-edit — see [[klorb-init]]. Neither file is derived from the other; they're maintained
  independently. Going forward, introducing a new config-file-exposed setting means adding a
  shipped-default entry to `default-config.json` — not necessarily to `template-config.json`,
  which stays minimal on purpose.
* Reasoning: The two jobs pull against each other on one axis — completeness. A file `klorb
  init` copies for hand-editing is best kept minimal: every key present is a key a user now
  has to look at (or ignore) in their own file, and a large copied file obscures which few
  settings are actually worth touching. A file that's silently merged into every process's
  `ProcessConfig`, by contrast, needs to be exhaustive: any recognized key it doesn't set
  falls back to a hardcoded Python field default instead, which is exactly the drift a
  single canonical defaults file exists to prevent. One file could only ever get one of
  "minimal, so `klorb init`'s copy stays readable" and "exhaustive, so every default has one
  canonical, on-disk source" right, not both. Splitting also changes `load_process_config()`'s
  actual behavior, not just its organization: before this split, the packaged reference file
  was purely descriptive — nothing but `klorb init` ever read it, so e.g.
  `sessionDefaults.readDirs.deny`'s dotfile denylist only existed in a file a user had to
  have already copied into place to benefit from. `default-config.json` is unconditionally
  merged on every process start instead, so that denylist (and every other shipped default)
  is now actually enforced out of the box, with no `klorb init` step as a prerequisite.
