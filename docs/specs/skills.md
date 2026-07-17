# Skills

A skill is a directory of instructions (and optional supporting files) that teaches the agent how
to do one bounded, reusable thing — write a `docs/specs/` file, add a CLI flag, source a component
— the same shape Claude's own skills take. A skill is invoked either explicitly, by the user
mentioning `/<skill-name>` in a prompt, or implicitly, when klorb judges an already-listed skill
description relevant to the current task. Every discoverable skill's `name` and one-line
`description` are compiled once, on the first turn, into an `<AvailableSkills>`
`<SystemInterjection>` riding on the first user prompt, so the model learns what's available
without a per-turn cost. `SearchSkills` narrows that list by keyword, and `ActivateSkill` loads one
skill's full `SKILL.md` into context.

## Skill directory layout and `SKILL.md`

A skill is a directory whose basename is its `name` — a lower-kebab-case slug — containing at
minimum a `SKILL.md`:

```
<skill-name>/
  SKILL.md
  <any other supporting files the skill's own instructions reference>
```

The directory basename is the sole source of the skill's `name`. `name` is validated as a bare
slug everywhere the model supplies one (`ActivateSkill`, `ReadSkillFile`): it must contain no path
separator (`/` or `\`) and must not be `.` or `..` (a name has no separators, so that is the only
way it could carry a `..` component). A directory whose basename fails this validation is skipped
during discovery; a tool call naming such a string raises `ValueError` before any disk access — so
`name` can never be steered into a path that escapes its harness-resolved namespace directory (the
same discipline `klorb.tools.memory.common.validate_memory_filename` enforces). The validation
lives in `klorb.tools.skill.common.validate_skill_name`/`is_valid_skill_name`.

`SKILL.md` opens with YAML frontmatter carrying a `description`, then a markdown body:

```markdown
---
description: >
  Add a new command-line flag to the klorb CLI, threaded through SessionConfig/Session so
  library code (TUI, future VSCode plugin) can see it.
---

<the skill's actual instructions>
```

`description` is the frontmatter field klorb reads. It's a single paragraph — no hard length cap,
but it is exactly what gets listed for the model (see "The available-skills interjection"), so a
skill author keeps it to a sentence or two. Frontmatter is parsed by
`klorb.tools.skill.common.parse_frontmatter_description`:

* **Parsing uses PyYAML's `yaml.safe_load`, never `yaml.load`.** This is a security requirement,
  not a nicety: a workspace-tier skill's frontmatter is project-supplied content a
  hostile-but-trusted repository could author, and `safe_load` refuses `!!python/object`-style
  tags, so parsing untrusted frontmatter can never construct arbitrary objects or execute code.
* **A missing `description`, a non-mapping document, malformed YAML, or any parse error is treated
  identically to an empty-string `description`** — never a discovery failure. The skill is still
  discoverable (it exists on disk); it simply contributes an empty description to the list.
* **A skill directory with no `SKILL.md` is ignored entirely** — not an error, just not a skill.

Supporting files (reference material, scripts, templates) live alongside `SKILL.md` and are read
via `ReadSkillFile` once the skill's own instructions point the model at them. Nothing about
loading a skill auto-loads its supporting files.

## Discovery tiers and precedence

Skills are discovered from three tiers, named by the namespace tokens `workspace`, `user`, and
`internal`. In permission rules and grants a skill's identity is the `(namespace, name)` pair.

* **Workspace** (namespace `workspace`): `${workspace_root}/.klorb/skills/*/SKILL.md`. Only
  discoverable when `SessionConfig.workspace.trusted` is `True` — an untrusted workspace
  contributes nothing to skill discovery at all, the same gate `projects-and-trust`, `memories`,
  and `workspace-context-files` apply, and for the same reason: a skill's body is instructions the
  agent is meant to *follow*, so a hostile, downloaded-and-unzipped repository shipping one is
  exactly the `workspace-context-files` risk this inherits rather than reopens. When
  `compatibility.claudeSkills` is enabled, `${workspace_root}/.claude/skills/` is discovered as a
  **second `workspace`-namespace source** alongside `.klorb/skills/` (see "Claude-skills
  compatibility" below).
* **User** (namespace `user`): `$KLORB_DATA_DIR/skills/*/SKILL.md` (default
  `~/.local/share/klorb/skills/`) — data-dir-rooted like `memories`'s `global` namespace, since a
  skill is closer to accumulated agent/user knowledge than to a scalar setting.
* **Packaged** (namespace `internal`): `klorb.resources/skills/`, shipped as package data inside
  the installed `klorb` distribution and read via `importlib.resources.files("klorb.resources")`,
  the same mechanism `system_prompts.d`'s packaged tier uses. This is where klorb's own built-in
  skills live, including `/create-edit-skill` (below).

All tiers are scanned fresh at discovery time (no caching layer). When the same `name` exists in
more than one tier (or in both workspace source dirs), the most specific tier wins outright —
workspace, then user, then packaged — and the others' copies of that name are not merged or
consulted at all, the same all-or-nothing shadowing `resolve_prompt_file()` uses. Precedence
shadows by `name`, but permission identity is the full `(namespace, name)` pair, so a lower tier
shadowing a higher tier's name does **not** inherit that higher tier's verdict — the shadowing
skill is a distinct `(namespace, name)` resource with its own verdict.

`klorb.tools.skill.common` exposes discovery as plain functions taking primitives (a workspace
root `Path`, a trust `bool`, the `compatibility.claudeSkills` flag, a `SkillRules`) rather than a
`ToolSetupContext`, so `klorb.session` can call them to build its interjections without the import
cycle the tool modules incur. The `internal` tier dir is resolved through
`internal_skills_dir()`, a one-line seam so tests can redirect it.

## The available-skills interjection

The `name` and `description` of every discoverable skill whose `(namespace, name)` does not
evaluate to `"deny"` are compiled, **once**, into a single `<SystemInterjection
subject="AvailableSkills">` block. This block is built at the first `Session.send_turn()` — from a
single disk scan of the tiers — and prepended onto that first turn's user `Message`, exactly like
the one-shot `ProjectGuidance` block that carries the workspace's context files (see
`workspace-context-files`). Once built, it is **locked for the rest of the session**: it is not
recompiled, and a skill added or removed mid-session is not reflected until a `/clear` starts a
fresh `Session`. This keeps the list off the system prompt entirely — the system prompt stays a
stable, cacheable prefix (`roles-and-system-prompts`), and a workspace-tier skill's
project-supplied `description` rides in a *user-turn* interjection the model can tell apart from
harness authority.

```
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

## Explicit `/skill-name` mentions

When a turn's own user prompt text contains `/<name>` for a `name` that is currently discoverable
*and* not `deny`-verdicted, `Session.send_turn()` prepends a `<SystemInterjection
subject="SkillReference">` block for that turn only, reminding the model of the skill's
`description` and that `ActivateSkill` is how to load it. This is a reminder, not the skill's full
body. A `/whatever` that doesn't resolve to a real, non-denied skill name produces no interjection
— it's just an ordinary slash, most commonly a path or a division sign. Only the user's own prompt
text (captured before any interjection is prepended) is scanned; the model's own output never
triggers a same-turn reminder (it can call `ActivateSkill` directly). Unlike the available-skills
list, this fires every turn a skill is textually mentioned, rescanning discovery live rather than
using the locked standing list.

## `SearchSkills`

`SearchSkills(queries: list[str])` matches each query as a literal, case-insensitive substring
against both a skill's `name` and its full `SKILL.md` body (frontmatter included) — the same
construction `SearchMemories` uses. Its result is a flat list of `{namespace, name, description}`
for every skill with a hit, no matched-line detail: since a skill's `name`/`description` are
already exposed by the available-skills interjection, `SearchSkills` exists to *narrow*, not to
reveal. It reads across all three tiers (respecting the workspace-trust gate) and requires no
`skillRules` check of its own, for the same reason. It **does** exclude any skill whose
`(namespace, name)` currently evaluates to `"deny"`, exactly as the available-skills interjection
does.

## Activating a skill

`ActivateSkill(namespace: str, name: str)` resolves the exact `(namespace, name)` pair against the
tiers and, if found, returns both the resolved skill's full `SKILL.md` content and a
recursively-enumerated, sorted `files` manifest of every regular file's path relative to the skill
directory (a `find -type f`-style list, `SKILL.md` included) — the model then follows those
instructions and reaches each supporting file through `ReadSkillFile` using exactly those relative
paths. `name` is validated as a bare slug first. A pair not found (including a `workspace` pair when
the workspace is untrusted, so the whole tier is skipped) is a plain `ValueError`, no permission
question raised.

Loading a skill's instructions is a materially bigger step than reading its name and one-line
description, so it's gated by a `skillRules` resource kind on `klorb.permissions.table.
PermissionsTable`:

* `SkillRules` (`klorb.permissions.skill_access`, a pydantic model mirroring `CommandRules`):
  `deny`/`ask`/`allow`, each a `list[tuple[str, str]]` of exact `(namespace, name)` pairs. Lives
  on `SessionConfig.skill_rules`, on-disk as `sessionDefaults.skillRules` (each entry a
  fully-qualified skill name string `"<namespace>/<name>"` — unambiguous since a name has no path
  separator, and friendlier in a hand-edited config than a nested array; `format_fqsn`/`parse_fqsn`
  in `klorb.permissions.skill_access` are the single serialization seam), concatenated across
  config layers exactly like `commandRules`.
* `SkillsAccessTable` matches by exact tuple equality only (like `FileAccessTable`'s exact-path
  equality, not `DirectoryAccessTable`'s containment). A pair matching no rule evaluates to `None`,
  normalized to `"ask"` by `normalize_skill_verdict` — the same "no permissive default" fallback
  `CommandAccessTable` uses, so a skill never activates merely because nothing denied it. Keying
  identity on `(namespace, name)` means a grant a user made for, say, `("internal",
  "create-edit-skill")` can never be inherited by a same-named `("workspace",
  "create-edit-skill")` skill a repository later ships to shadow it.
* `klorb.tools.skill.common.raise_if_skill_not_allowed` enforces the verdict before any content is
  read: `"allow"` (or a one-shot `PermissionOverride.skills` covering the pair) returns; `"deny"`
  raises `PermissionError`; `"ask"` raises `PermissionAskRequired` carrying a new `skill:
  tuple[str, str] | None` slot (alongside the existing `path`). `Session._run_tool_calls` treats a
  skill ask exactly like a directory ask: it dispatches through `on_permission_ask` (with
  `PermissionAskContext.skill` set), and `_retry_after_permission_decision`/`_apply_ask_grant`
  apply the grant. A `scope="once"` retry carries the pair on `PermissionOverride.skills`; a
  persistent-scope grant goes through `klorb.permissions.skill_grant.apply_skill_permission_grant`
  (mirroring `command_grant`). A persisted grant records the full `(namespace, name)` pair.
* Packaged skills expected to be safe by default (starting with `/create-edit-skill`) are
  pre-populated into `skillRules.allow` — as `"internal/<name>"` strings — by
  `klorb.resources/default-config.json`, the same way that file pre-populates `readFiles.allow` for
  `/dev/null`. Because the entry names the `internal` namespace explicitly, a workspace- or
  user-tier skill of the same name does not inherit its `allow`.

## Supporting files: `ReadSkillFile`

`ReadSkillFile(namespace: str, name: str, path: str)` resolves the skill's directory the same
harness-resolved way `ActivateSkill` does, then resolves `path` as a safe relative path confined to
that directory: it must be relative (no leading `/` or `~`) and contain no `..` component, and — for
a real-filesystem tier — its symlink-resolved result must still be within the skill directory (the
same canonicalize-then-containment defense `memories` applies). It reads via
`klorb.tools.util.ReadFileCore`, so it offers the same line-range mechanics as `ReadFile`.

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

`SearchSkills`, `ActivateSkill`, and `ReadSkillFile` read `.klorb/skills/`, `.claude/skills/`,
`$KLORB_DATA_DIR/skills/`, and the packaged tier directly, the same way `memories`'s tools read
`.klorb/memories/` and `$KLORB_DATA_DIR/memories/`: a harness-resolved namespace directory plus a
validated bare `name` (and, for `ReadSkillFile`, a validated relative `path`), never a
model-supplied path into the rest of the filesystem, so there's nothing for `readDirs`/the `.klorb`
self-tampering protection to usefully protect against — see the scratchpad-tools-bypass ADR for the
precedent. This is a separate axis from `skillRules`: a skill being *readable* by these tools says
nothing about whether `ActivateSkill` is *permitted* to hand its content to the model — that's what
`skillRules` alone decides.

## Creating and editing skills

There is no `CreateSkill`/`EditSkill` tool. A workspace- or user-tier skill is authored the same
way any other privileged-directory file is: `EscalatePrivileges(scope="workspace")` (for
`.klorb/skills/...`) or `EscalatePrivileges(scope="homedir")` (for `$KLORB_DATA_DIR/skills/...`)
followed by ordinary `CreateFile`/`EditFile`/`ReadFile` calls. The packaged (`internal`) tier is
never writable this way — a new built-in skill is added to the klorb source tree. Because this is a
convention-heavy, multi-step dance, the instructions live as klorb's own packaged skill,
`klorb.resources/skills/create-edit-skill/SKILL.md`, rather than in a dedicated tool — "how to
build a skill" is itself just a skill.

## Claude-skills compatibility (`compatibility.claudeSkills`)

`compatibility.claudeSkills` (a top-level `klorb-config.json` key backing
`ProcessConfig.compatibility_claude_skills`, default `false`) is a compatibility shim for projects
that carry Claude-Code-style skills under `.claude/skills/`, mirroring `compatibility.claudeMarkdown`
for `CLAUDE.md`. When enabled and the workspace is trusted, `${workspace_root}/.claude/skills/` is
discovered as a **second source for the `workspace` namespace**, alongside `.klorb/skills/` — not a
fourth namespace. Skills from either source share the `workspace` identity for permission purposes.
On a name collision, `.klorb/skills/` (klorb's own convention) wins over `.claude/skills/`, per the
same most-specific-source-wins shadowing every tier uses. Claude Code's own `SKILL.md` frontmatter
may carry fields beyond `description` (a `name`, allowed-tools lists, model hints); klorb reads only
`description` and ignores the rest, so a Claude-authored `SKILL.md` is discovered by its directory
basename with its `description` listed. See
docs/adrs/discover-claude-skills-dir-as-a-second-workspace-source.md.

## Configuration

* `sessionDefaults.skillRules` — `{"deny": [...], "ask": [...], "allow": [...]}`, each a list of
  fully-qualified skill-name strings `"<namespace>/<name>"` (`namespace` one of `workspace`/`user`/
  `internal`), backing `SessionConfig.skill_rules`. Concatenated across config layers like
  `commandRules`.
* `compatibility.claudeSkills` — `bool`, default `false` (see above).
* No `klorb-config.json` key controls *discovery* — the tier locations are fixed, scanned
  unconditionally subject to the workspace-trust gate.

## Known risks

* **A trusted workspace's own config layer can pre-`allow` its own workspace-tier skills.** Once a
  workspace is trusted, its `.klorb/klorb-config.json` — the least-trusted config layer — is read,
  and its `skillRules.allow` entries concatenate into the list every other layer's rules are
  evaluated against. So a trusted repository could ship both `.klorb/skills/foo/` and a
  `.klorb/klorb-config.json` granting `"workspace/foo"`, making `foo` activate with no ask.
  This is the same shape as the `readDirs.allow` known risk in `permissions`, accepted for the same
  reason: reaching this state requires an explicit interactive decision to trust the workspace,
  which vouches for both its shipped skills and its config file. Keying grants on `(namespace,
  name)` closes the strictly worse variant — a workspace skill *hijacking* a grant the user made
  for a same-named `internal`/`user` skill. Mitigation: a user- or `/etc`-level `skillRules.deny`
  for a `(namespace, name)` always outranks any `allow`, since `deny` is evaluated first.

## Out of scope

* **Vector-indexed skill search.** `SearchSkills` is a literal substring match; an embedding-based
  index is separate follow-up work.
* **Pruning the available-skills list.** Every non-denied skill is compiled into the first-turn
  interjection regardless of count; a recency/frequency-based top-*k* cutoff is anticipated but not
  built.
* **Pinned tool results.** An activated skill's `SKILL.md` content lives only as an `ActivateSkill`
  tool-result message; under context summarization it can be compacted away mid-task. A general
  "pinned tool result" mechanism is real follow-up work, not designed here.
* **Executing bundled skill scripts.** `ReadSkillFile` reads a skill's supporting files; running a
  bundled script via `BashTool` is a separate sandbox/permissions question.
* **Glob/wildcard skill-name rules.** `SkillRules` matches exact `(namespace, name)` pairs only.
* **Role-scoped skill repertoires.** `Role.repertoire()` is a placeholder; this skill list is
  role-agnostic — every session sees every discoverable, non-denied skill.
* **Subagent inheritance.** How a spawned subagent's skill state relates to its parent's is
  unaddressed; today there is only one session.
* **Hot-reload guarantees.** The available-skills interjection is compiled once, at the first turn,
  and locked; `SearchSkills`/`ActivateSkill`/`ReadSkillFile` rescan on demand. A skill added
  mid-session won't appear in the standing list until a `/clear`.
