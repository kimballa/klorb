# Evaluate PermissionsTable rules by category (deny, then ask, then allow), and merge config layers by concatenation

* Date: 2026-07-02 01:30
* Question: `PermissionsTable` governs a resource (the first instance: directory access, via
  `readDirs`/`writeDirs`) using three rule lists — `deny`, `ask`, `allow` — each populated by
  merging up to five layered `klorb-config.json` sources (`/etc/klorb` → per-user → per-project
  → `--config` → last-session, in increasing precedence). Two questions had to be answered
  together: within one table, when a candidate matches rules in more than one category, which
  wins — and does specificity (a narrower rule) ever override a broader one in a different
  category? And across config layers, does a higher-precedence layer's rule replace a
  lower-precedence layer's rule for the same table, the way every other `klorb-config.json` key
  already works (`dict.update`, last layer wins)?
* Answer: Rules are evaluated by fixed category order — `deny`, then `ask`, then `allow` — and
  the first category with any matching rule wins, full stop. Rule specificity never changes
  this order: a broad `deny` always beats a narrower `allow` for the same path. Across config
  layers, `readDirs`/`writeDirs` are **not** merged like every other key. Instead, each layer's
  `deny`/`ask`/`allow` arrays are **concatenated** onto the accumulated list for that category,
  in layer order — a deliberate, documented exception to `load_process_config()`'s otherwise
  universal `dict.update()`, last-layer-wins merge (see `docs/specs/process-and-session-config.md`).
* Reasoning: Concatenation plus fixed category-order evaluation together give one load-order-
  independent invariant, and it's the whole point of this design: **a stricter rule
  contributed by any layer, including the lowest-precedence `/etc/klorb/klorb-config.json`, can
  never be overridden by a looser rule from a higher-precedence layer** (user config, project
  config, `--config`). This falls directly out of the mechanics — once lists are concatenated,
  there is no "layer N's rule replaces layer M's rule" step left anywhere in the pipeline; every
  entry from every layer is an undifferentiated member of its category, and `deny` is always
  checked before `ask` and `allow` regardless of which layer supplied which entry. Duplicate or
  directly conflicting entries across layers (the same path in one layer's `allow` and
  another's `deny`) are therefore harmless and require no special-casing or validation at
  merge time — the conflict resolves itself at evaluation time, in favor of the stricter
  category, every time.

  This matters because `readDirs`/`writeDirs`' own precedence model is upside-down from every
  other setting in the file: for a scalar setting like `model`, a project's `.klorb/klorb-config.json`
  is trusted to override the user's own preference, because overriding a preference is low
  stakes. Permission *grants* are not low stakes, and the project layer is the *least* trusted
  one (it can arrive via an untrusted, cloned repository's `cwd` — see
  `docs/adrs/gate-read-hard-boundary-on-workspace-trust.md` for the resulting residual risk on
  the read side). Replace-and-layer-order semantics would let a project's config silently
  widen access a user or administrator had intentionally restricted; concatenate-and-category-
  order semantics make that structurally impossible — the only way to remove a `deny` a
  higher-trust layer set is to edit or delete that layer's own file, which requires whatever
  privilege guards that file already (e.g. root, for `/etc/klorb/klorb-config.json`).

  The alternative considered — keep `dict.update()`-style replacement, and let a later layer's
  `readDirs`/`writeDirs` object replace an earlier layer's outright, same as
  `thinking.tokenBudgets` — was rejected because it would let *any* layer erase a stricter
  layer's denials just by declaring its own (possibly empty) `readDirs`/`writeDirs` object,
  defeating the purpose of having a permission system with layered authority at all.
