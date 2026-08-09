# Skills

A skill is a directory of instructions (and optional supporting files) that teaches the agent how
to do one bounded, reusable thing — write a `docs/specs/` file, add a CLI flag, source a component
— the same shape Claude's own skills take. A skill is invoked either explicitly, by the user
mentioning `/<skill-name>` (or `/<namespace>:<skill-name>`) in a prompt, or implicitly, when klorb
judges an already-listed skill description relevant to the current task. Every discoverable
skill's `name` and one-line `description` are compiled once, on the first turn, into an
`<AvailableSkills>` `<SystemInterjection>` riding on the first user prompt, so the model learns
what's available without a per-turn cost. `SearchSkills` narrows that list by keyword, and
`ActivateSkill` loads one skill's full `SKILL.md` into context.

## Skill directory layout and `SKILL.md`

A skill is a directory whose basename is its canonical `name` — a lower-kebab-case slug —
containing at minimum a `SKILL.md`:

```text
<skill-name>/
  SKILL.md
  <any other supporting files the skill's own instructions reference>
```

The directory basename is always the skill's canonical `name` — the identity every `skillRules`
rule and approval decision is keyed on. `name` is validated as a bare slug everywhere the model
supplies one (`ActivateSkill`, `ReadSkillFile`) or a directory/frontmatter name is discovered from
disk: it must contain no path separator (`/` or `\`) or `:` (the fully-qualified-skill-name
separator — see "Fully-qualified skill names" below), must not be `.` or `..` (a name has no
separators, so that is the only way it could carry a `..` component), must not start or end with
`-` (ambiguous with a CLI-flag-style token), and must contain no `<`/`>` (would corrupt the
`<SystemInterjection>`/skill-list markup a name rides in). A tool call naming a string that fails
this raises `ValueError` before any disk access — so `name` can never be steered into a path that
escapes its harness-resolved namespace directory (the same discipline `klorb.tools.memory.common.
validate_memory_filename` enforces). A directory basename or frontmatter alias failing it is
skipped during discovery, logged as a `logger.warning()` since it's worth surfacing to whoever
authored the skill. The validation lives in `klorb.tools.skill.common.validate_skill_name`/
`is_valid_skill_name` — one predicate covering every rule above, checked identically regardless of
where the candidate name came from.

The canonical `name` every catalog entry is keyed on is derived from the directory basename,
lowercased and capped to `MAX_SKILL_NAME_DISPLAY_LENGTH` (64 characters,
`klorb.tools.skill.common.display_skill_name`) — what's advertised to the model and a user-facing
skill list (the vscode-plugin fuzzy finder, backed by `Session.discover_skills()`) is always this
lowercased, length-capped form, and it's also the *only* form the catalog dict is ever keyed on:
`ActivateSkill`/`ReadSkillFile` resolve against exactly what was advertised, so a name once shown
to the model is always resolvable, even when the real directory name is longer. The full
(untruncated) lowercased basename is still resolvable too, but only as a `typed`-catalog alias (see
"The session-scoped skill catalog" below) — never the canonical identity itself. Two skills whose
basenames collide only after lowercasing/truncation resolve to whichever one `resolve_all_skills()`
yields first (a logged, dropped collision), the same shape as an alias collision below.
`display_skill_name` also strips any trailing `-` truncation would otherwise leave behind (e.g. a
name whose 65th character is `-`), so the capped identity always satisfies `is_valid_skill_name`
the same way the real, un-truncated name already did.

`SKILL.md` opens with YAML frontmatter carrying `name` and `description`, then a markdown body:

```markdown
---
name: add-cli-flag
description: >
  Add a new command-line flag to the klorb CLI, threaded through SessionConfig/Session so
  library code (TUI, future VSCode plugin) can see it.
---

<the skill's actual instructions>
```

`klorb.tools.skill.common.parse_frontmatter` parses the whole frontmatter block into a raw
`dict[str, Any]` (`{}` on any parse problem — see below); `description` and `name` are two
attributes read out of it, but a skill author may write others (Claude Code's own frontmatter
carries more — see "Claude-skills compatibility"). `klorb.tools.skill.model.Skill.raw` holds that
whole dict, so a future feature can read a new attribute without a frontmatter-schema change.

* **`description`** is a single paragraph — no hard length cap on `Skill.description` itself
  (propagated straight from `raw["description"]`; a missing or non-string value is `""`), but
  every agent-facing surface that displays it (the available-skills/`SkillReference` bullet lists,
  `SearchSkills` results) truncates it to `MAX_SKILL_DESCRIPTION_DISPLAY_LENGTH` (1024 characters)
  via `klorb.tools.skill.common.display_skill_description` — a defense against a hostile or
  careless frontmatter description bloating every turn's context, so a skill author keeps it to a
  sentence or two regardless.
* **`disable-model-invocation`** (`bool`, default unset/`False`): when `true`, the skill is never
  added to the catalog `ActivateSkill`/`ReadSkillFile` resolve against — only to the `typed`
  catalog a user's own `/<name>` mention resolves against — so it's invisible to the
  available-skills interjection and `SearchSkills`, and unreachable by a model that merely guesses
  its name. See "Model-invocation-disabled skills" below.
* **`name`** should match the directory basename. It's how the doc calls this "should", not
  "must": the directory basename is *always* the canonical name — nothing else could be, since
  precedence, `skillRules`, and approval decisions all need one identity nailed down before a
  frontmatter file is even parsed. When the frontmatter `name` disagrees with the basename, the
  catalog builder logs a `logger.warning()` (see "The session-scoped skill catalog" below) and moves
  on: the skill is still discoverable under its basename, and the frontmatter `name` becomes
  usable as an *alias* a user may type instead — see `Skill.aliases` below — but klorb itself
  (`ActivateSkill`, `ReadSkillFile`, `skillRules`, every interjection) only ever resolves and
  displays the canonical basename, never the alias.

Frontmatter parsing:

* **Parsing uses PyYAML's `yaml.safe_load`, never `yaml.load`.** This is a security requirement,
  not a nicety: a workspace-tier skill's frontmatter is project-supplied content a
  hostile-but-trusted repository could author, and `safe_load` refuses `!!python/object`-style
  tags, so parsing untrusted frontmatter can never construct arbitrary objects or execute code.
* **A missing frontmatter block, a non-mapping document, malformed YAML, or any parse error yields
  `{}`** — never a discovery failure. The skill is still discoverable (it exists on disk); it
  simply contributes an empty `raw` dict (and so an empty description, and no alias) to the
  catalog.
* **A skill directory with no `SKILL.md` is ignored entirely** — not an error, just not a skill.

Supporting files (reference material, scripts, templates) live alongside `SKILL.md` and are read
via `ReadSkillFile` once the skill's own instructions point the model at them. Nothing about
loading a skill auto-loads its supporting files.

## Discovery tiers and precedence

Skills are discovered from three tiers, named by the namespace tokens `user`, `workspace`, and
`internal` — in that order, `klorb.permissions.skill_access.VALID_NAMESPACES`, most- to
least-specific. In permission rules and grants a skill's identity is the `(namespace, name)` pair.

* **User** (namespace `user`): `$KLORB_DATA_DIR/skills/*/SKILL.md` (default
  `~/.local/share/klorb/skills/`) — data-dir-rooted like `memories`'s `global` namespace, since a
  skill is closer to accumulated agent/user knowledge than to a scalar setting. **Highest
  precedence**: a homedir skill overrides a same-named workspace or internal skill.
* **Workspace** (namespace `workspace`): `${workspace_root}/.klorb/skills/*/SKILL.md`. Only
  discoverable when `SessionConfig.workspace.trusted` is `True` — an untrusted workspace
  contributes nothing to skill discovery at all, the same gate `projects-and-trust`, `memories`,
  and `workspace-context-files` apply, and for the same reason: a skill's body is instructions the
  agent is meant to *follow*, so a hostile, downloaded-and-unzipped repository shipping one is
  exactly the `workspace-context-files` risk this inherits rather than reopens. When
  `compatibility.claudeSkills` is enabled, `${workspace_root}/.claude/skills/` is discovered as a
  **second `workspace`-namespace source** alongside `.klorb/skills/` (see "Claude-skills
  compatibility" below).
* **Internal** (namespace `internal`): `klorb.resources/skills/`, shipped as package data inside
  the installed `klorb` distribution and read via `importlib.resources.files("klorb.resources")`,
  the same mechanism `system_prompts.d`'s packaged tier uses. This is where klorb's own built-in
  skills live, including `/create-edit-skill` (below). **Lowest precedence.**

When the same `name` exists in more than one tier (or in both workspace source dirs), the
most-specific tier wins outright — user, then workspace, then internal (and, within the workspace
tier, `.klorb/skills/` before `.claude/skills/`) — and the others' copies of that name are not
merged or consulted at all, the same all-or-nothing shadowing `resolve_prompt_file()` uses. This
precedence shadows *which tier a bare, unqualified `name` means* (see "The session-scoped skill
catalog" below); it does not remove the shadowed tier's copy from the catalog outright — a
lower-precedence skill of the same name is still resolvable directly by its exact `(namespace,
name)` pair (e.g. `ActivateSkill(namespace="internal", name="foo")`, or a typed
`/internal:foo` reference), and its permission verdict is entirely its own: a grant made for one
`(namespace, name)` pair is never inherited by a same-named pair in a different namespace.

`klorb.tools.skill.common` exposes the raw disk scan as plain functions taking primitives (a
workspace root `Path`, a trust `bool`, the `compatibility.claudeSkills` flag) rather than a
`ToolSetupContext`, so the catalog builder (below) can call them without the import cycle the tool
modules incur. The `internal` tier dir is resolved through `internal_skills_dir()`, a one-line
seam so tests can redirect it.

## The session-scoped skill catalog

Nothing that resolves a skill at runtime — `SearchSkills`, `ActivateSkill`, `ReadSkillFile`, or any
`Session` interjection — walks the filesystem itself. Instead, each `Session` owns one
`klorb.tools.skill.catalog.SkillCatalogRegistry` instance (`Session.skill_catalog_registry`, built
fresh in `SessionCoreMixin.__init__` alongside its `ToolRegistry` — never a module-level global) that
holds two `SkillCatalog`s (each just a `{(namespace, name): Skill}` dict plus lookup/derived-view
methods), built from a **single** disk scan, and every subsequent lookup for that session reads them
in memory through a method call on that instance. A `Tool`'s `apply()` reaches its session's registry
via `context.session.skill_catalog_registry`, most commonly through the
`klorb.tools.skill.catalog.resolve_session_skill_catalog_registry()` convenience helper (which also
calls `ensure_from_context()` and raises `ValueError` if `context` wasn't built with a real
`Session`); `Session`'s own skill interjections (`klorb.session.mixins.skills.SessionSkillsMixin`)
use `self._skill_catalog_registry` directly:

* **`registry.canonical()`** is keyed by every discovered skill's true `(namespace, name)` identity
  — its directory basename, lowercased and capped to `MAX_SKILL_NAME_DISPLAY_LENGTH`. This is the
  *only* catalog `ActivateSkill`/`ReadSkillFile` may resolve against (`resolve_and_gate_skill`), and
  the only identity `skillRules` rules and approval decisions are ever keyed on. A skill whose
  `disable-model-invocation` frontmatter flag is `true` is never added here (see
  "Model-invocation-disabled skills" below).
* **`registry.typed()`** additionally carries an alias entry for each string in `Skill.aliases` that
  differs from the canonical `(namespace, name)` — up to three more entries (the full untruncated
  basename, and the frontmatter `name` in both its full and capped forms, when present and valid)
  — pointing at the *same* `Skill` object as its canonical entry. This is the catalog a user's typed
  reference is checked against (`SkillCatalog.resolve_reference()`, see "Explicit skill mentions"
  below). An alias can never shadow another skill's real `(namespace, name)` identity: if it
  collides with a genuine skill's canonical name (or another skill's alias), the alias is dropped
  (logged) and the earlier-registered skill wins. A `disable-model-invocation` skill *is* still
  added here, under its canonical name and every alias — this is the one catalog it's ever
  resolvable through.

A skill whose `(namespace, name)` verdict against the session's *current* `skillRules` is already
`"deny"` at the moment `build_catalogs()` scans it is excluded from **both** catalogs entirely —
not merely filtered out of `discoverable()` below. Since a `"deny"` verdict for a given
`(namespace, name)` pair can never become anything else within one built catalog's lifetime (the
catalog isn't rebuilt just because `skillRules` changed), there both isn't and shouldn't be a way
to reach it — it's absent from the available-skills interjection, `SearchSkills`, the vscode-plugin
fuzzy finder (`Session.discover_skills()`, the `_klorb/listSkills` ACP extension's own
implementation), and any `/<name>` mention, exactly as if it didn't exist on disk. This is stronger
than a skill denied *after* the catalog was already built (e.g. an interactive ask answered "deny"
mid-session): that one stays resolvable in memory until an explicit reload, so its `skillRules`
verdict is still checked at every use — a leading `/<name>` mention for it is skipped with a logged
`logger.warning()` (see "Leading skill mention" below), and `ActivateSkill`/`ReadSkillFile` still
raise `PermissionError` for it, same as ever.

Both catalogs are built once, lazily, the first time either is needed in that session (via
`registry.ensure()` — a cheap no-op once built), and stay in memory for the rest of that
`Session`'s life. **Scoped to the `Session`, not the process**: a `/clear` that replaces the live
`Session` gets a brand-new, empty `SkillCatalogRegistry`, which rescans the disk on its own first
use rather than inheriting whatever the outgoing session's catalog held — see
docs/adrs/00167-scope-skill-catalogs-to-session-not-process.md. Skill catalogs are never persisted to
`session.json`; a restored session rebuilds its catalog the same way a brand-new one does, on its
own first use. Within one session's lifetime, a skill added, removed, or edited on disk after the
catalog was built is still invisible until an explicit `Session.reload_skills()` call — the
**"Reload skills"** command-palette action (reachable via `ctrl+p` or by typing `>reload skills` in
the prompt, `klorb.tui.commands.skill_commands.SkillCommandProvider`) and the `_klorb/reloadSkills`
ACP extension both call it — which rebuilds both catalogs from a fresh scan against the session's
current workspace and reports the resulting skill count. `Session.reload_skills()` is also what a
workspace trust-state change (`_apply_workspace_config`) calls internally, so a newly-trusted
workspace's `.klorb/skills/` tier becomes visible immediately rather than only after an explicit
reload.

`SkillCatalogRegistry` itself holds no free-standing module-level mutable state: its `_typed`/
`_canonical` fields are private instance attributes, reset only through its own methods (`ensure()`
and `reload()`), and `build_catalogs()` — the pure, stateless disk-scan function `reload()` calls —
returns a `SkillCatalogs` bundle (`.typed`/`.canonical` fields) rather than a positional tuple.

`SkillCatalog.precedence_deduped()` computes the "one winning `Skill` per bare name" view described
above, entirely from the already-built `SkillCatalogRegistry.canonical()` — no disk access.
`SkillCatalog.discoverable(skill_rules)` further filters that to non-`"deny"`-verdicted skills; it
is what the available-skills interjection lists and `SearchSkills` narrows (see below).

## `klorb.tools.skill.model.Skill`

Every catalog entry is a `Skill`, a pydantic `BaseModel`:

* **`namespace`**/**`name`** — the canonical `(namespace, name)` identity (`name` is the directory
  basename, lowercased and length-capped — see above).
* **`description`** — propagated straight from `raw["description"]` (`""` if absent/non-string).
* **`raw`** — the skill's whole parsed YAML frontmatter dict, whatever attributes its author wrote
  (see `parse_frontmatter` above).
* **`aliases`** — a `set[str]` of every string a user typing `/<name>` may use to mean this skill
  (via `SkillCatalogRegistry.typed()`; klorb's own resolution — `SkillCatalogRegistry.canonical()`
  — never consults it): the full (untruncated) lowercased basename, the canonical (capped) basename,
  and — when the frontmatter `name` disagrees with the basename and is itself valid — both its full
  and capped forms too. Up to four strings, deduped by set semantics whenever some of them coincide
  (the common case: a basename under the length cap collapses "full" and "capped" to one entry).
* **`root`** — the skill directory's `Traversable` (a real `Path` for the `workspace`/`user`
  tiers, or an `importlib.resources` `Traversable` for a zip-installed `internal` tier), used to
  read `SKILL.md`/supporting files on demand. Bodies are *not* cached on the `Skill` object itself
  — only frontmatter is — so the catalog stays cheap to hold even for a large skill.

## Fully-qualified skill names

A skill's fully-qualified name (fqsn) is `"<namespace>:<name>"` — a colon, not `/`, is the
separator (`klorb.permissions.skill_access.format_fqsn`/`parse_fqsn`). A colon is unambiguous
because a skill name can never itself contain one (`is_valid_skill_name` above); `/` is reserved
for how a user *mentions* a skill in prompt text (`/foo`, `/namespace:foo`), so it can't double as
the fqsn separator too. This is the format used for:

* **`skillRules` config entries** — `sessionDefaults.skillRules.{deny,ask,allow}` are lists of
  `"<namespace>:<name>"` strings (e.g. `"internal:create-edit-skill"`), parsed by `parse_fqsn` and
  skipped (not a crash) if malformed or missing the colon. See "Configuration" below.
* **A colon-qualified prompt mention** — `/internal:my-skill` in a user's prompt text parses as an
  fqsn and resolves *only* that exact `(namespace, name)` pair, in either catalog, or nothing at
  all (never falling back to a bare-name search across tiers) — see `SkillCatalog.
  resolve_reference()`.

## The available-skills interjection

The `name` and `description` of every discoverable skill whose `(namespace, name)` does not
evaluate to `"deny"` are compiled, **once**, into a single `<SystemInterjection
subject="AvailableSkills">` block (`SkillCatalog.discoverable()`, precedence-deduped — a
lower-precedence tier's same-named skill isn't listed twice, or at all, here). This block is built
at the first `Session.send_turn()` and prepended onto that first turn's user `Message`, exactly
like the one-shot `ProjectGuidance` block that carries the workspace's context files (see
`workspace-context-files`). Once built, it is **locked for the rest of the session**: it is not
recompiled, even if `>reload skills` rebuilds the underlying catalog mid-session — a fresh
`/clear` is what picks up the change. This keeps the list off the system prompt entirely — the
system prompt stays a stable, cacheable prefix (`roles-and-system-prompts`), and a workspace-tier
skill's project-supplied `description` rides in a *user-turn* interjection the model can tell apart
from harness authority.

```text
<SystemInterjection subject="AvailableSkills">
The following skills are available. ...
- add-cli-flag (workspace): Add a new command-line flag to the klorb CLI...
- create-edit-skill (internal): How to author or edit a klorb skill...
</SystemInterjection>
```

A skill whose `(namespace, name)` evaluates to `"deny"` is excluded entirely — there's no reason to
advertise a skill the model structurally cannot activate. A skill evaluating to `"ask"` or
`"allow"` is listed the same way; the difference only shows up when `ActivateSkill` is called. A
`disable-model-invocation` skill is excluded too, regardless of verdict — it was never in
`registry.canonical()` (the catalog `discoverable()` enumerates) to begin with. Listing every
other non-denied skill is a deliberate first-version simplification; a future recency/frequency-
based top-*k* cutoff is anticipated (see "Out of scope").

Every bullet's `name` is already the catalog's own (capped) identity — see "Skill directory layout"
above — so it needs no further truncation here; `description` is capped at display time
(`klorb.tools.skill.common.display_skill_description`, 1024 characters), the same cap
`SearchSkills` results use (see below), since a frontmatter description is arbitrary free text with
no length limit of its own, unlike `name`.

## Explicit skill mentions

When a turn's own user prompt text contains `/<token>` (a bare name, or a colon-qualified
`/<namespace>:<name>`) for a token that resolves against `SkillCatalogRegistry.typed()` (see "Fully-qualified
skill names") to a skill whose verdict isn't `"deny"`, `Session.send_turn()` prepends a
`<SystemInterjection subject="SkillReference">` block for that turn only, reminding the model of
the skill's canonical `description` and that `ActivateSkill` is how to load it — always by
canonical name, never whichever alias the user may have typed. This is a reminder, not the skill's
full body. A `/whatever` that doesn't resolve to a real, non-`"deny"` skill produces no
interjection — it's just an ordinary slash, most commonly a path or a division sign. Only the
user's own prompt text (captured before any interjection is prepended) is scanned; the model's own
output never triggers a same-turn reminder (it can call `ActivateSkill` directly). This fires every
turn a skill is textually mentioned, reading the (already-built, in-memory) catalog fresh each
time, unlike the locked available-skills list above.

`send_turn()` extracts every `/<token>` slug from the prompt (a cheap, regex scan, no catalog
lookups) before resolving any of them — a prompt with no `/` in it at all costs nothing beyond that
regex, since there's nothing a lookup could possibly match.

### Leading skill mention: `UserSkillActivation`

When the user's prompt *starts* with a skill reference — the first non-whitespace character is
`/`, and that leading `/<token>` resolves to a real skill — the harness treats this as an
**unconditional activation**, not a casual mention: it's what "the user ran `/foo ...`" as an
invocation, rather than "the user happened to write `/foo` somewhere in their message", is meant to
feel like.

If the resolved skill's canonical `(namespace, name)` verdict is `"allow"` — or `"ask"`, see
below — `Session.send_turn()` prepends a `<SystemInterjection subject="UserSkillActivation">`
block carrying the exact same `{namespace, name, content, files, tokens}` JSON payload
`ActivateSkill` would return for that skill — built by `klorb.tools.skill.common.
skill_activation_payload()`, the single piece of code both `ActivateSkillTool.apply()` and this
mechanism share, so the two paths can never drift apart. The message explains: *"The user has
invoked skill \<name\>. Read the skill JSON that follows plus the user's prompt, then apply this
skill:"* followed by the JSON. After the interjection, the user's message body continues exactly
as they typed it (including the leading `/<token>` itself — nothing is stripped out of the prompt
text).

This only applies to the *leading* mention. A prompt like `/skill-1 bla bla /skill-2` gets a
`UserSkillActivation` block for `skill-1` and a separate, ordinary `SkillReference` reminder for
`skill-2` (mentioned elsewhere in the same message) — `skill-1` is excluded from that reminder
list since it already got the full activation treatment.

**Typing the leading `/<name>` mention itself counts as the user's approval.** If the verdict is
`"ask"`, `_build_user_skill_activation_interjection` auto-promotes it to `"allow"` for the rest of
this session — `klorb.permissions.skill_grant.apply_skill_permission_grant(action="allow",
scope="session", ...)`, the same in-memory mutation a `"session"`-scope answer to an interactive
ask would apply — and then proceeds exactly as the already-`"allow"` case above, with **no
interactive ask panel raised**. This only ever widens `"ask"` to `"allow"`; it never touches a
`"deny"` verdict, and the promotion is session-scoped only (never written to a config file), the
same as any other `"session"`-scope grant. If the verdict is `"deny"`, the leading mention gets no
special treatment at all — as if the message hadn't started with a skill reference, matching every
other `"deny"`-verdicted skill's invisibility elsewhere — except when the skill was resolvable in
this session's (unrebuilt) catalog at a less restrictive verdict and was denied only afterward
(e.g. an interactive ask elsewhere in the session was answered "deny" mid-session): that case logs
a `logger.warning()` so the otherwise-silent skip is still observable, since a pre-denied skill (see
"The session-scoped skill catalog" above) can't reach this code path in the first place.

**A UI hook, not just a stored interjection.** Alongside the interjection, `Session.send_turn()`
invokes `TurnEventHandlers.on_skill_activated(skill_id)` if the caller supplied one, so a live UI
doesn't have to re-parse the interjection out of stored message content to know a skill just
activated. The TUI wires this to `show_notice()`, appending an `Activated skill: <namespace>/<name>`
line to the history right after the echoed prompt; the vscode-plugin webview instead recognizes the
`UserSkillActivation` subject when it renders a restored/streamed history entry's system
interjections (`HistoryView.tsx`'s `SystemInterjection` component) and renders the same friendly
label in place of the generic collapsed "System interjection (...)" disclosure every other subject
gets. A restored TUI history scroll (`_mount_restored_history`,
`SubagentsPanelMixin._render_restored_messages`) shows the same notice too, via
`klorb.tui.formatting.extract_skill_activation_notice`, even though the interjection body itself
stays stripped from the displayed message like any other.

## `SearchSkills`

`SearchSkills(queries: list[str])` matches each query as a literal, case-insensitive substring
against both a skill's `name` and its full `SKILL.md` body (frontmatter included) — the same
construction `SearchMemories` uses. Its result is a flat list of `{namespace, name, description}`
for every skill with a hit, no matched-line detail: since a skill's `name`/`description` are
already exposed by the available-skills interjection, `SearchSkills` exists to *narrow*, not to
reveal. It searches `SkillCatalogRegistry.canonical().discoverable(skill_rules)` — the same precedence-deduped,
non-`"deny"` set the available-skills interjection lists (so a `disable-model-invocation` skill,
never in `canonical()`, is unreachable here too) — reading each candidate's `SKILL.md` body fresh
(the catalog doesn't cache skill bodies, only frontmatter) to match against the body text. Each
result's `description` is capped the same way the available-skills interjection's is (see above);
`name` needs no separate capping, since it's already the catalog's own identity.

## Activating a skill

`ActivateSkill(namespace: str, name: str)` resolves the exact `(namespace, name)` pair against
`SkillCatalogRegistry.canonical()` and, if found, returns both the resolved skill's full `SKILL.md` content and
a recursively-enumerated, sorted `files` manifest of every regular file's path relative to the
skill directory (a `find -type f`-style list, `SKILL.md` included) — the model then follows those
instructions and reaches each supporting file through `ReadSkillFile` using exactly those relative
paths. `name` is validated as a bare slug first. A pair not found (including a `workspace` pair
that was never in the catalog because the workspace was untrusted when it was built) is a plain
`ValueError`, no permission question raised. The manifest walk applies the same symlink-
canonicalization containment check `ReadSkillFile` applies to a `path` argument (see below): a
symlink inside the skill directory that resolves outside it is excluded from the manifest entirely,
rather than followed and leaked into what the model sees.

### Model-invocation-disabled skills

`resolve_and_gate_skill` (the shared front half of `ActivateSkill`/`ReadSkillFile`) resolves
`(namespace, name)` against `canonical()` first; a `disable-model-invocation` skill was never added
there (see "The session-scoped skill catalog"), so this lookup always misses for it. Rather than
the generic "no such skill" `ValueError` an actually-unknown name gets, `resolve_and_gate_skill`
also checks `typed()` at that point purely to give a caller that *guessed* such a skill's name (it
was never advertised, so the only way to know it exists at all is to have somehow learned its
name) a specific, actionable refusal instead: *"Skill \<fqsn\> cannot be loaded by name -- it only
activates when the user's own message starts with \"/\<name\>\". Tell the user to invoke it that
way if they want to use it; do not retry this call."* This is a final safety check, not a
disclosure — the `typed()` lookup only ever informs the error message, never resolves or returns
the skill's content. The one legitimate way in stays the leading-mention `UserSkillActivation`
path above, which resolves through `typed()` directly and never calls `ActivateSkill` at all.

Loading a skill's instructions is a materially bigger step than reading its name and one-line
description, so it's gated by a `skillRules` resource kind on `klorb.permissions.table.
PermissionsTable`:

* `SkillRules` (`klorb.permissions.skill_access`, a pydantic model mirroring `CommandRules`):
  `deny`/`ask`/`allow`, each a `list[tuple[str, str]]` of exact `(namespace, name)` pairs. Lives
  on `SessionConfig.skill_rules`, on-disk as `sessionDefaults.skillRules` (each entry a
  fully-qualified skill name string `"<namespace>:<name>"` — see "Fully-qualified skill names"
  above), concatenated across config layers exactly like `commandRules`.
* `SkillsAccessTable` matches by exact tuple equality only (like `FileAccessTable`'s exact-path
  equality, not `DirectoryAccessTable`'s containment). A pair matching no rule evaluates to `None`,
  normalized to `"ask"` by `normalize_skill_verdict` — the same "no permissive default" fallback
  `CommandAccessTable` uses, so a skill never activates merely because nothing denied it. Keying
  identity on `(namespace, name)` means a grant a user made for, say, `("internal",
  "create-edit-skill")` can never be inherited by a same-named `("workspace",
  "create-edit-skill")` skill a repository later ships to shadow it — and, per the alias rule
  above, never by a frontmatter alias either: approval decisions are always with respect to a
  skill's *canonical* fully-qualified name.
* `klorb.tools.skill.common.raise_if_skill_not_allowed` (called by `klorb.tools.skill.catalog.
  resolve_and_gate_skill`, the shared front half of `ActivateSkill`/`ReadSkillFile`) enforces the
  verdict before either tool hands any of the skill's content to the model: `"allow"` returns;
  `"deny"` raises `PermissionError`; `"ask"`
  raises `PermissionAskRequired` carrying a new `skill: tuple[str, str] | None` slot (alongside the
  existing `path`), or returns instead when a one-shot `PermissionOverride.skills` covers the pair
  -- the override is only ever consulted for an `"ask"` verdict, never a `"deny"` one, so it can
  retry a skill the user was just asked about but can never resurrect one the table denies
  outright. The security property this protects is *disclosure to the model*, not disk I/O
  ordering: `resolve_and_gate_skill` reads the skill's `description` (already resident on the
  catalog's `Skill` object) before this check runs, purely so the ask/deny message can name what
  the skill does for the user's benefit -- that's metadata read for the permission prompt itself,
  never content returned to the model, so it's fine for it to precede the verdict check.
  `Session._run_tool_calls` treats a skill ask exactly like a directory ask: it dispatches through
  `on_permission_ask` (with `PermissionAskContext.skill` set), and
  `_retry_after_permission_decision`/`_apply_ask_grant` apply the grant. A `scope="once"` retry
  carries the pair on `PermissionOverride.skills`; a persistent-scope grant goes through
  `klorb.permissions.skill_grant.apply_skill_permission_grant` (mirroring `command_grant`, both
  built on the shared `klorb.permissions.rule_grant_base.RuleGrantWriter` scaffolding). A persisted
  grant records the full `(namespace, name)` pair.
* Packaged skills expected to be safe by default (starting with `/create-edit-skill`) are
  pre-populated into `skillRules.allow` — as `"internal:<name>"` strings — by
  `klorb.resources/default-config.json`, the same way that file pre-populates `readFiles.allow` for
  `/dev/null`. Because the entry names the `internal` namespace explicitly, a workspace- or
  user-tier skill of the same name does not inherit its `allow`.

## Supporting files: `ReadSkillFile`

`ReadSkillFile(namespace: str, name: str, path: str)` resolves the skill against
`SkillCatalogRegistry.canonical()` the same way `ActivateSkill` does, then resolves `path` as a safe relative
path confined to that skill's directory: it must be relative (no leading `/` or `~`) and contain
no `..` component, and — for a real-filesystem tier — its symlink-resolved result must still be
within the skill directory (the same canonicalize-then-containment defense `memories` applies). It
reads via `klorb.tools.util.ReadFileCore`, so it offers the same line-range mechanics as `ReadFile`.

`ReadFileCore` reads through an overridable `open_resource()` seam: a real filesystem `Path` is
opened with the builtin `open()`, and any other `importlib.resources` `Traversable` is opened via
its own `.open()`. This is what makes `ReadSkillFile` work for an `internal`-tier skill file even
when klorb is installed as a zip/wheel whose packaged resources have no filesystem path — the file
is read through the resource loader, not `open()`.

Reading a supporting file is gated by the skill's own `skillRules` verdict, but raises no new ask
beyond activation: `ReadSkillFile` requires the skill's `(namespace, name)` to evaluate to
`"allow"` (or to have been granted this session), and otherwise raises the *same* activation ask
`ActivateSkill` would. A `"deny"` skill's files are unreadable.

## Filesystem access: bypassing `readDirs`, not participating in it

The catalog builder (`klorb.tools.skill.catalog.build_catalogs`, via `klorb.tools.skill.common`'s
disk-scan primitives) reads `.klorb/skills/`, `.claude/skills/`, `$KLORB_DATA_DIR/skills/`, and the
packaged tier directly, the same way `memories`'s tools read `.klorb/memories/` and
`$KLORB_DATA_DIR/memories/`: a harness-resolved namespace directory plus a validated bare `name`
(and, for `ReadSkillFile`, a validated relative `path`), never a model-supplied path into the rest
of the filesystem, so there's nothing for `readDirs`/the `.klorb` self-tampering protection to
usefully protect against — see the scratchpad-tools-bypass ADR for the precedent. This is a
separate axis from `skillRules`: a skill being *readable* by this scan says nothing about whether
`ActivateSkill` is *permitted* to hand its content to the model — that's what `skillRules` alone
decides.

## Creating and editing skills

There is no `CreateSkill`/`EditSkill` tool. A workspace- or user-tier skill is authored the same
way any other privileged-directory file is: `EscalatePrivileges(scope="workspace")` (for
`.klorb/skills/...`, and for `.claude/skills/...` when `compatibility.claudeSkills` is enabled —
see below) or `EscalatePrivileges(scope="homedir")` (for `$KLORB_DATA_DIR/skills/...`) followed by
ordinary `CreateFile`/`EditFile`/`ReadFile` calls. The packaged (`internal`) tier is never writable
this way — a new built-in skill is added to the klorb source tree. Because this is a
convention-heavy, multi-step dance, the instructions live as klorb's own packaged skill,
`klorb.resources/skills/create-edit-skill/SKILL.md`, rather than in a dedicated tool — "how to
build a skill" is itself just a skill. A newly-created or edited skill isn't visible to
`SearchSkills`/`ActivateSkill`/the interjections until the catalog is rebuilt — `>reload skills`,
or a fresh session.

## Claude-skills compatibility (`compatibility.claudeSkills`)

`compatibility.claudeSkills` (a top-level `klorb-config.json` key backing
`ProcessConfig.compatibility_claude_skills`, default `false`) is a compatibility shim for projects
that carry Claude-Code-style skills under `.claude/skills/`, mirroring `compatibility.claudeMarkdown`
for `CLAUDE.md`. When enabled and the workspace is trusted, `${workspace_root}/.claude/skills/` is
discovered as a **second source for the `workspace` namespace**, alongside `.klorb/skills/` — not a
fourth namespace. Skills from either source share the `workspace` identity for permission purposes.
On a name collision, `.klorb/skills/` (klorb's own convention) wins over `.claude/skills/`, per the
same most-specific-source-wins shadowing every tier uses. When the flag is enabled,
`${workspace_root}/.claude/skills/` also becomes a privileged directory requiring
`EscalatePrivileges(scope="workspace")` to write to, the same as `.klorb/skills/` — see
"Internal privileged paths" in docs/specs/permissions.md. Claude Code's own `SKILL.md` frontmatter
may carry fields beyond `name`/`description` (allowed-tools lists, model hints, etc.); klorb reads
only `name`/`description` off the raw frontmatter dict and leaves the rest in `Skill.raw` unused
today, so a Claude-authored `SKILL.md` is discovered by its directory basename with its
`description` listed. See docs/adrs/00134-discover-claude-skills-dir-as-a-second-workspace-source.md.

## Configuration

* `sessionDefaults.skillRules` — `{"deny": [...], "ask": [...], "allow": [...]}`, each a list of
  fully-qualified skill-name strings `"<namespace>:<name>"` (`namespace` one of `user`/`workspace`/
  `internal`), backing `SessionConfig.skill_rules`. Concatenated across config layers like
  `commandRules`.
* `compatibility.claudeSkills` — `bool`, default `false` (see above).
* No `klorb-config.json` key controls *discovery* — the tier locations are fixed, scanned
  unconditionally subject to the workspace-trust gate, once per session (see "The session-scoped
  skill catalog").

## Known risks

* **A trusted workspace's own config layer can pre-`allow` its own workspace-tier skills.** Once a
  workspace is trusted, its `.klorb/klorb-config.json` — the least-trusted config layer — is read,
  and its `skillRules.allow` entries concatenate into the list every other layer's rules are
  evaluated against. So a trusted repository could ship both `.klorb/skills/foo/` and a
  `.klorb/klorb-config.json` granting `"workspace:foo"`, making `foo` activate with no ask.
  This is the same shape as the `readDirs.allow` known risk in `permissions`, accepted for the same
  reason: reaching this state requires an explicit interactive decision to trust the workspace,
  which vouches for both its shipped skills and its config file. Keying grants on `(namespace,
  name)` closes the strictly worse variant — a workspace skill *hijacking* a grant the user made
  for a same-named `internal`/`user` skill. Mitigation: a user- or `/etc`-level `skillRules.deny`
  for a `(namespace, name)` always outranks any `allow`, since `deny` is evaluated first.
* **A stale catalog.** Since each `Session`'s catalog is built once, from a single disk scan, a
  skill added, edited, or removed on disk after that point is invisible to that session until
  `>reload skills` is run explicitly (a brand-new `Session` — a fresh interactive session, a
  restored one, or a `/clear` — is unaffected, since it rescans on its own first use). This is a
  deliberate performance/simplicity trade rather than an oversight; a future version could
  auto-invalidate on a filesystem-watch signal.
* **A leading `/<name>` mention auto-promotes an `"ask"`-verdicted skill to `"allow"` with no
  interactive prompt.** This is intentional, not an oversight: typing a skill's name as the leading
  token of a message is treated as the user's own approval, the same weight an interactive "Allow
  (this session)" answer carries — see "Leading skill mention". It only ever widens `"ask"`, never
  `"deny"`, and only for this session (never persisted to a config file), so the worst case is one
  extra session-scoped `allow` the user could have gotten anyway by typing `/<name>` a second time
  and clicking through the ask panel.

## Out of scope

* **Vector-indexed skill search.** `SearchSkills` is a literal substring match; an embedding-based
  index is separate follow-up work.
* **Pruning the available-skills list.** Every non-denied skill is compiled into the first-turn
  interjection regardless of count; a recency/frequency-based top-*k* cutoff is anticipated but not
  built.
* **Pinned tool results.** An activated skill's `SKILL.md` content lives only as an `ActivateSkill`
  tool-result message (or a `UserSkillActivation` interjection); under context summarization it can
  be compacted away mid-task. A general "pinned tool result" mechanism is real follow-up work, not
  designed here.
* **Executing bundled skill scripts.** `ReadSkillFile` reads a skill's supporting files; running a
  bundled script via `BashTool` is a separate sandbox/permissions question.
* **Glob/wildcard skill-name rules.** `SkillRules` matches exact `(namespace, name)` pairs only.
* **Role-scoped skill repertoires.** `Role.repertoire()` is a placeholder; this skill list is
  role-agnostic — every session sees every discoverable, non-denied skill.
* **Subagent inheritance.** How a spawned subagent's skill state relates to its parent's is
  unaddressed; today there is only one session.
* **Automatic catalog invalidation.** Within one session's lifetime, its catalog is rebuilt only on
  an explicit `>reload skills`; there's no filesystem watch or staleness detection.
