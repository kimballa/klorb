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
supplies one (`ActivateSkill`, `ReadSkillFile`): it must contain no path separator (`/` or `\`) or
`:` (the fully-qualified-skill-name separator — see "Fully-qualified skill names" below), and must
not be `.` or `..` (a name has no separators, so that is the only way it could carry a `..`
component). A directory whose basename fails this validation is skipped during discovery; a tool
call naming such a string raises `ValueError` before any disk access — so `name` can never be
steered into a path that escapes its harness-resolved namespace directory (the same discipline
`klorb.tools.memory.common.validate_memory_filename` enforces). The validation lives in
`klorb.tools.skill.common.validate_skill_name`/`is_valid_skill_name`.

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

* **`description`** is a single paragraph — no hard length cap, but it is exactly what gets listed
  for the model (see "The available-skills interjection"), so a skill author keeps it to a
  sentence or two. Propagated straight from `raw["description"]` onto `Skill.description`; a
  missing or non-string value is `""`.
* **`name`** should match the directory basename. It's how the doc calls this "should", not
  "must": the directory basename is *always* the canonical name — nothing else could be, since
  precedence, `skillRules`, and approval decisions all need one identity nailed down before a
  frontmatter file is even parsed. When the frontmatter `name` disagrees with the basename, the
  catalog builder logs a `logger.warning()` (see "The process-wide skill catalog" below) and moves
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
precedence shadows *which tier a bare, unqualified `name` means* (see "The process-wide skill
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

## The process-wide skill catalog

Nothing that resolves a skill at runtime — `SearchSkills`, `ActivateSkill`, `ReadSkillFile`, or any
`Session` interjection — walks the filesystem itself. Instead, `klorb.tools.skill.catalog` builds
two `SkillCatalog`s (each just a `{(namespace, name): Skill}` dict plus lookup/derived-view
methods) from a **single** disk scan, and every subsequent lookup reads them in memory:

* **`canonical_catalog()`** is keyed by every discovered skill's true `(namespace, name)` identity
  — its directory basename. This is the *only* catalog `ActivateSkill`/`ReadSkillFile` may resolve
  against (`resolve_and_gate_skill`), and the only identity `skillRules` rules and approval
  decisions are ever keyed on.
* **`typed_catalog()`** additionally carries an alias entry `(namespace, <frontmatter name>)` for a
  skill whose frontmatter `name` disagrees with its directory basename (see above) — pointing at
  the *same* `Skill` object as its canonical entry. This is the catalog a user's typed reference is
  checked against (`SkillCatalog.resolve_reference()`, see "Explicit skill mentions" below). An
  alias can never shadow another skill's real `(namespace, name)` identity: if a frontmatter alias
  collides with a genuine skill's canonical name, the alias is dropped (logged) and the real skill
  wins.

Both catalogs are built once, lazily, the first time either is needed in the process (via
`ensure_skill_catalog()` — a cheap no-op once built), and stay in memory for the rest of the
process's life, **independent of any one `Session`**: a `/clear` that replaces the live `Session`
does *not* rebuild the catalog, since the catalog is keyed off the workspace/trust/compat
parameters the *first* caller supplied, not off whichever `Session` happens to be asking. A skill
added, removed, or edited on disk after that point is invisible until an explicit
`reload_skill_catalog()` call — the **"Reload skills"** command-palette action (reachable via
`ctrl+p` or by typing `>reload skills` in the prompt, `klorb.tui.commands.skill_commands.
SkillCommandProvider`) rebuilds both catalogs from a fresh scan against the current session's
workspace, and reports the resulting skill count.

`SkillCatalog.precedence_deduped()` computes the "one winning `Skill` per bare name" view described
above, entirely from the already-built `canonical_catalog()` — no disk access.
`SkillCatalog.discoverable(skill_rules)` further filters that to non-`"deny"`-verdicted skills; it
is what the available-skills interjection lists and `SearchSkills` narrows (see below).

## `klorb.tools.skill.model.Skill`

Every catalog entry is a `Skill`, a pydantic `BaseModel`:

* **`namespace`**/**`name`** — the canonical `(namespace, name)` identity (`name` is always the
  directory basename).
* **`description`** — propagated straight from `raw["description"]` (`""` if absent/non-string).
* **`raw`** — the skill's whole parsed YAML frontmatter dict, whatever attributes its author wrote
  (see `parse_frontmatter` above).
* **`aliases`** — a `set[str]` containing the directory basename plus the frontmatter `name`, when
  present, valid, and different from the basename. This is exactly the set of strings a user
  typing `/<name>` may use to mean this skill (via `typed_catalog()`); klorb's own resolution
  (`canonical_catalog()`) never consults it.
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
`"allow"` is listed the same way; the difference only shows up when `ActivateSkill` is called.
Listing every non-denied skill is a deliberate first-version simplification; a future
recency/frequency-based top-*k* cutoff is anticipated (see "Out of scope").

## Explicit skill mentions

When a turn's own user prompt text contains `/<token>` (a bare name, or a colon-qualified
`/<namespace>:<name>`) for a token that resolves against `typed_catalog()` (see "Fully-qualified
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

If the resolved skill's canonical `(namespace, name)` verdict is `"allow"`, `Session.send_turn()`
prepends a `<SystemInterjection subject="UserSkillActivation">` block carrying the exact same
`{namespace, name, content, files, tokens}` JSON payload `ActivateSkill` would return for that
skill — built by `klorb.tools.skill.common.skill_activation_payload()`, the single piece of code
both `ActivateSkillTool.apply()` and this mechanism share, so the two paths can never drift apart.
The message explains: *"The user has invoked skill \<name\>. Read the skill JSON that follows plus
the user's prompt, then apply this skill:"* followed by the JSON. After the interjection, the
user's message body continues exactly as they typed it (including the leading `/<token>` itself —
nothing is stripped out of the prompt text).

This only applies to the *leading* mention. A prompt like `/skill-1 bla bla /skill-2` gets a
`UserSkillActivation` block for `skill-1` and a separate, ordinary `SkillReference` reminder for
`skill-2` (mentioned elsewhere in the same message) — `skill-1` is excluded from that reminder
list since it already got the full activation treatment.

**A leading mention never bypasses `skillRules` approval.** If the verdict is `"ask"`, no content
is auto-injected — the leading mention instead falls back to the ordinary `SkillReference`
reminder, so the model still has to call `ActivateSkill` and go through the normal interactive
approval flow. If the verdict is `"deny"`, the leading mention gets no special treatment at all —
as if the message hadn't started with a skill reference, matching every other `"deny"`-verdicted
skill's invisibility elsewhere.

## `SearchSkills`

`SearchSkills(queries: list[str])` matches each query as a literal, case-insensitive substring
against both a skill's `name` and its full `SKILL.md` body (frontmatter included) — the same
construction `SearchMemories` uses. Its result is a flat list of `{namespace, name, description}`
for every skill with a hit, no matched-line detail: since a skill's `name`/`description` are
already exposed by the available-skills interjection, `SearchSkills` exists to *narrow*, not to
reveal. It searches `canonical_catalog().discoverable(skill_rules)` — the same precedence-deduped,
non-`"deny"` set the available-skills interjection lists — reading each candidate's `SKILL.md` body
fresh (the catalog doesn't cache skill bodies, only frontmatter) to match against the body text.

## Activating a skill

`ActivateSkill(namespace: str, name: str)` resolves the exact `(namespace, name)` pair against
`canonical_catalog()` and, if found, returns both the resolved skill's full `SKILL.md` content and
a recursively-enumerated, sorted `files` manifest of every regular file's path relative to the
skill directory (a `find -type f`-style list, `SKILL.md` included) — the model then follows those
instructions and reaches each supporting file through `ReadSkillFile` using exactly those relative
paths. `name` is validated as a bare slug first. A pair not found (including a `workspace` pair
that was never in the catalog because the workspace was untrusted when it was built) is a plain
`ValueError`, no permission question raised. The manifest walk applies the same symlink-
canonicalization containment check `ReadSkillFile` applies to a `path` argument (see below): a
symlink inside the skill directory that resolves outside it is excluded from the manifest entirely,
rather than followed and leaked into what the model sees.

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
`canonical_catalog()` the same way `ActivateSkill` does, then resolves `path` as a safe relative
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
`.klorb/skills/...`) or `EscalatePrivileges(scope="homedir")` (for `$KLORB_DATA_DIR/skills/...`)
followed by ordinary `CreateFile`/`EditFile`/`ReadFile` calls. The packaged (`internal`) tier is
never writable this way — a new built-in skill is added to the klorb source tree. Because this is a
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
same most-specific-source-wins shadowing every tier uses. Claude Code's own `SKILL.md` frontmatter
may carry fields beyond `name`/`description` (allowed-tools lists, model hints, etc.); klorb reads
only `name`/`description` off the raw frontmatter dict and leaves the rest in `Skill.raw` unused
today, so a Claude-authored `SKILL.md` is discovered by its directory basename with its
`description` listed. See docs/adrs/discover-claude-skills-dir-as-a-second-workspace-source.md.

## Configuration

* `sessionDefaults.skillRules` — `{"deny": [...], "ask": [...], "allow": [...]}`, each a list of
  fully-qualified skill-name strings `"<namespace>:<name>"` (`namespace` one of `user`/`workspace`/
  `internal`), backing `SessionConfig.skill_rules`. Concatenated across config layers like
  `commandRules`.
* `compatibility.claudeSkills` — `bool`, default `false` (see above).
* No `klorb-config.json` key controls *discovery* — the tier locations are fixed, scanned
  unconditionally subject to the workspace-trust gate, once per process (see "The process-wide
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
* **A stale catalog.** Since the catalog is built once per process and not tied to any one
  `Session`, a skill added, edited, or removed on disk mid-process is invisible to every session
  running in that process until `>reload skills` is run explicitly. This is a deliberate
  performance/simplicity trade rather than an oversight; a future version could auto-invalidate on
  a filesystem-watch signal.

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
* **Automatic catalog invalidation.** The catalog is rebuilt only on an explicit `>reload skills`
  or a fresh process; there's no filesystem watch or staleness detection.
