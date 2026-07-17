# Skills

> **Draft.** This spec describes a design for review, not a built feature. Nothing under
> `docs/plans/drafting/` should be treated as implemented until it's promoted out of this
> directory.

## Summary

A skill is a directory of instructions (and optional supporting files) that teaches the agent
how to do one bounded, reusable thing — write a docs/specs/ file, source a BOM component, add a
CLI flag — the same shape Claude's own skills take. A skill is invoked either explicitly, by the
user mentioning `/<skill-name>` in a prompt, or implicitly, when klorb judges an already-listed
skill description relevant to the current task. Every discoverable skill's `name` (its
directory's basename, a lower-kebab-case slug) and one-line `description` are compiled once, at
the first turn, into an `<AvailableSkills>` `<SystemInterjection>` riding on the first user
prompt — so the model learns what's available without a per-turn cost or a lookup; `SearchSkills`
narrows that list by keyword when there are enough skills that the standing list alone isn't
enough, and `ActivateSkill` loads one skill's full `SKILL.md` into context. Skills are discovered
from three tiers — workspace (`${workspace_root}/.klorb/skills/`), user
(`$KLORB_DATA_DIR/skills/`), and packaged (`klorb.resources.skills`) — the same three-tier shape
[[projects-and-trust]] and [[roles-and-system-prompts]] already use for workspace-local,
per-user, and built-in content, with an untrusted workspace's tier skipped entirely, mirroring
[[memories]]'s own workspace-namespace gate. In permission rules and grants these three tiers are
named by the namespace tokens `workspace`, `user`, and `internal` respectively.

Loading a skill's content is gated by a new `skillRules` deny/ask/allow table
([[permissions]]'s `PermissionsTable` abstraction, matching skills by exact `(namespace, name)`
identity) enforced on `ActivateSkill`, so the first activation of a given skill interrupts for
the user's approval exactly like a `BashTool` command rule does — see "Activating a skill" below.
Reading skill files off disk is a separate concern from that approval gate:
`SearchSkills`/`ActivateSkill`/`ReadSkillFile` read directly from all three tiers, including
`.klorb/skills/` and `$KLORB_DATA_DIR`, bypassing `readDirs`/the `.klorb` self-tampering
protection entirely — the same bypass [[memories]] and `docs/specs/scratchpad.md`'s tools use,
for the same reason: `name` is a validated bare-slug identifier into a harness-resolved
directory, never a model-supplied path into the rest of the filesystem. There is deliberately no
`CreateSkill`/`EditSkill` tool; authoring or editing a skill goes through `EscalatePrivileges`
(already covers both `.klorb/` and `$KLORB_DATA_DIR` — no new scope needed) followed by the
ordinary `ReadFile`/`EditFile`/`CreateFile` tools, walked through by a packaged,
internally-provided meta-skill, `/create-edit-skill` — see "Creating and editing skills" below.

## How it works

### Skill directory layout and `SKILL.md`

A skill is a directory whose basename is its `name` — a lower-kebab-case slug — containing at
minimum a `SKILL.md`:

```
<skill-name>/
  SKILL.md
  <any other supporting files the skill's own instructions reference>
```

The directory basename is the sole source of the skill's `name`: there is no `name` field in the
frontmatter to disagree with it (see below). `name` is validated as a bare slug everywhere the
model supplies one (`ActivateSkill`, `SearchSkills` results, `ReadSkillFile`): it must contain no
path separator (`/` or `\`) and no `..` component. A directory whose basename fails this
validation is skipped during discovery, and a tool call naming such a string raises `ValueError`
before any disk access — so `name` can never be steered into a path that escapes its
harness-resolved namespace directory (the same discipline [[memories]]'s
`validate_memory_filename` enforces).

`SKILL.md` opens with YAML frontmatter carrying a single field, `description`, then a markdown
body:

```markdown
---
description: >
  Add a new command-line flag to the klorb CLI, threaded through SessionConfig/Session
  so library code (TUI, future VSCode plugin) can see it.
---

<the skill's actual instructions>
```

`description` is the only frontmatter field. It's a single paragraph — no hard length cap, but it
is exactly what gets listed for the model (see "The available-skills interjection" below), so a
skill author is expected to keep it to a sentence or two.

* **Frontmatter is parsed with PyYAML's `yaml.safe_load`, never `yaml.load`.** This is a security
  requirement, not a nicety: a workspace-tier skill's frontmatter is project-supplied content a
  hostile-but-trusted repository could author, and `safe_load` refuses `!!python/object`-style
  tags, so parsing untrusted frontmatter can never construct arbitrary objects or execute code.
  PyYAML (a new, small, ubiquitous dependency) is preferred over `ruamel.yaml` because the latter's
  advantage — round-trip comment/format preservation — only matters for *editing*, and discovery
  is strictly read-only.
* **A missing `description`, malformed YAML, or any parse error is treated identically to an
  empty-string `description`** — never a discovery failure. The skill is still discoverable (it
  exists on disk); it simply contributes an empty description to the list.
* **A skill directory with no `SKILL.md` is ignored entirely** — not an error, just not a skill.

Supporting files (reference material, scripts, templates) live alongside `SKILL.md`. They are
read via the dedicated `ReadSkillFile` tool once the skill's own instructions point the model at
them — see "Supporting files" below. Nothing about loading a skill auto-loads its supporting
files.

### Discovery tiers and precedence

* **Workspace** (namespace `workspace`): `${workspace_root}/.klorb/skills/*/SKILL.md`, built from
  `klorb.permissions.directory_access.KLORB_PROJECT_DIR_NAME` the same way
  `docs/specs/workspace-context-files.md`'s `.klorb/INSTRUCTIONS.md` is. Only discoverable when
  `SessionConfig.workspace.trusted` is `True` — an untrusted workspace contributes nothing to
  skill discovery at all, the same gate [[projects-and-trust]] and [[memories]]'s `workspace`
  namespace already apply, and for the same reason: a skill's body is instructions the agent is
  meant to *follow*, so a hostile, downloaded-and-unzipped repository shipping one is exactly
  the [[workspace-context-files]] risk this spec inherits rather than reopens.
* **User** (namespace `user`): `$KLORB_DATA_DIR/skills/*/SKILL.md` (default
  `~/.local/share/klorb/skills/`) — data-dir-rooted like [[memories]]'s `global` namespace, not
  `$KLORB_CONFIG_DIR`, since a skill is closer to accumulated agent/user knowledge than to a
  scalar setting.
* **Packaged** (namespace `internal`): `klorb.resources.skills`, shipped as package data inside
  the installed `klorb` distribution and read via `importlib.resources.files("klorb.resources")`,
  the same mechanism `system_prompts.d`'s packaged tier uses — see
  [the package-data ADR](../../adrs/ship-system-prompts-as-package-data-with-user-config-overrides.md).
  This is where klorb's own built-in skills live, including `/create-edit-skill` (below).

All three tiers are scanned fresh at discovery time (no caching layer yet — see "Out of scope").
When the same `name` exists in more than one tier, the most specific tier wins outright —
workspace, then user, then packaged — and the others' copies of that name are not merged or
consulted at all, the same all-or-nothing shadowing `resolve_prompt_file()` uses across its own
two tiers. Note that precedence shadows by `name`, but permission identity is keyed on the full
`(namespace, name)` pair (see "Activating a skill"), so a lower tier shadowing a higher tier's
name does **not** inherit that higher tier's verdict — the shadowing skill is a distinct
`(namespace, name)` resource with its own verdict.

### The available-skills interjection

The `name` and `description` of every discoverable skill whose `(namespace, name)` does not
currently evaluate to `"deny"` (see "Activating a skill") are compiled, **once**, into a single
`<SystemInterjection subject="AvailableSkills">` block. This block is built at the first
`Session.send_turn()` — from a single, one-time disk scan of the three tiers — and prepended onto
that first turn's user `Message`, exactly like the one-shot `<SystemInterjection
subject="ProjectGuidance">` block that carries the workspace's context files (see
[[workspace-context-files]] and `_wrap_system_interjection()` in `klorb.session`). Once built, it
is **locked for the rest of the session**: it is not recompiled, and a skill file added or
removed mid-session is not reflected until a `/clear` starts a fresh `Session`. This deliberately
keeps the list off the system prompt entirely — the system prompt stays a stable, cacheable
prefix ([[roles-and-system-prompts]]), and a workspace-tier skill's project-supplied
`description` rides in a *user-turn* interjection the model can tell apart from harness authority,
the same trust placement [[workspace-context-files]] chose for `.klorb/INSTRUCTIONS.md` and
`AGENTS.md` rather than folding them into the system prompt.

```
<SystemInterjection subject="AvailableSkills">
- add-cli-flag: Add a new command-line flag to the klorb CLI...
- create-edit-skill: How to create or edit a klorb skill using EscalatePrivileges and the file tools...
</SystemInterjection>
```

A skill whose `(namespace, name)` evaluates to `"deny"` is excluded from this list entirely —
there's no reason to advertise a skill the model structurally cannot activate. A skill evaluating
to `"ask"` or `"allow"` is listed the same way; the difference only shows up when `ActivateSkill`
is actually called. This unconditional listing of every non-denied skill is a deliberate
first-version simplification: once the number of installed skills makes even a once-per-session
list wasteful, it's expected to be pruned to a top-*k* subset (by recency, usage frequency, ...)
— not built here; see "Out of scope."

### Explicit `/skill-name` mentions

When a turn's own user prompt text contains `/<name>` for a `name` that is currently discoverable
*and* not `deny`-verdicted, `Session.send_turn()` prepends a `<SystemInterjection
subject="SkillReference">` block for that turn only (via the same `_wrap_system_interjection()`
helper the `PermissionFramework` and `ProjectGuidance` interjections use — see [[permissions]]'s
"Permission framework change interjection" section and [[workspace-context-files]]), reminding
the model of the skill's `description` and that `ActivateSkill(namespace="<ns>", name="<name>")`
is how to load it. This is a reminder, not the skill's full body — loading the body still goes
through `ActivateSkill` and its permission gate below, same as an implicit match would. A
`/whatever` that doesn't resolve to a real, non-denied skill name produces no interjection — it's
just an ordinary slash in the user's prompt text, most commonly a path or a division sign, not a
magic trigger. Only the user's own prompt text is scanned for this; there is no path by which the
model's own output triggers a same-turn reminder (it can call `ActivateSkill` directly). Unlike
`ProjectGuidance`, this fires at most once per *turn* it's textually present in, not once per
session — a later turn that mentions the same skill again gets the reminder again.

### `SearchSkills`

Takes `queries: list[str]`, matched as literal, case-insensitive substrings against both a
skill's `name` and its full `SKILL.md` body (frontmatter included) — the same
`klorb.tools.util.search_core` construction [[memories]]'s `SearchMemories` uses, minus the
line-level detail: since a skill's `name`/`description` are already fully exposed by the
available-skills interjection (above), `SearchSkills` exists to *narrow*, not to reveal — its
result is a flat list of `{namespace, name, description}` for every skill with a hit, no
matched-line detail. It reads across all three tiers (respecting the workspace-trust gate on the
workspace tier) and requires no `skillRules` check of its own, for the same reason: it discloses
nothing beyond what the standing list already put in context. **It does, however, explicitly
exclude any skill whose `(namespace, name)` currently evaluates to `"deny"`** — a denied skill is
omitted from search results exactly as it's omitted from the available-skills interjection, so
`SearchSkills` never re-surfaces a skill the model structurally cannot activate.

### Activating a skill

`ActivateSkill(namespace: str, name: str)` resolves the `(namespace, name)` pair against the
three discovery tiers and, if found, returns a structured result carrying **both** the resolved
skill's full `SKILL.md` content **and** a recursively-enumerated, sorted list of every regular
file's path *relative to the skill directory* (a `find -type f`-style manifest) — the model is
then expected to follow those instructions for the rest of the task at hand, the same way it
follows any other tool-supplied context. Bundling the manifest into the activation result (rather
than adding a separate skill-file-enumeration tool) is deliberate: the model learns what
supporting files exist the moment it activates the skill, and reaches each one through
`ReadSkillFile` (below) using exactly those relative paths. `name` is validated as a bare slug
first (see "Skill directory layout"). A pair not found is a plain `ValueError`, no permission
question raised.

Loading a skill's instructions is a materially bigger step than reading its name and one-line
description — the model starts *acting* on arbitrary, possibly workspace-supplied text — so it's
gated by a new resource kind on `klorb.permissions.table.PermissionsTable`:

* `SkillRules` (pydantic model, mirroring `CommandRules`): `deny`/`ask`/`allow`, each a
  `list[tuple[str, str]]` of exact `(namespace, name)` pairs — where `namespace` is one of
  `"workspace"`, `"user"`, `"internal"`. No glob/wildcard matching in a first version (see "Out
  of scope"). Lives on `SessionConfig.skill_rules`, on-disk as `sessionDefaults.skillRules`
  (each entry a two-element `["<namespace>", "<name>"]` array), concatenated across config layers
  exactly like `readDirs`/`writeDirs`/`commandRules` (see [[permissions]]'s "Configuration"
  section and
  [the category-order ADR](../../adrs/evaluate-permission-categories-deny-then-ask-then-allow.md)).
* `SkillsAccessTable(PermissionsTable[tuple[str, str]])` matches by exact tuple equality only
  (like `FileAccessTable`'s exact-path equality, not `DirectoryAccessTable`'s containment). A pair
  matching no rule in any list evaluates to `None` from `evaluate()`, normalized to `"ask"` — the
  same "no permissive default" fallback `CommandAccessTable` uses, so a skill never activates
  merely because nothing explicitly denied it. Keying identity on `(namespace, name)` rather than
  a bare name is deliberate: it means a grant a user made for, say, `("internal",
  "create-edit-skill")` can never be inherited by a same-named `("workspace",
  "create-edit-skill")` skill a repository later ships to shadow it — the shadowing skill is a
  distinct resource that must earn its own verdict.
* `ActivateSkillTool.apply()` calls `raise_if_not_allowed()` against this table before reading
  anything off disk. `"deny"` raises `PermissionError`; `"ask"` raises `PermissionAskRequired`
  carrying a `PermissionAskItem` extended with a new slot, `skill: tuple[str, str] | None`,
  alongside the existing `path`/`command` — the natural next case for the
  [`PermissionOverride` generalization ADR](../../adrs/generalize-permission-override-to-a-set-of-resources.md),
  which already anticipated further resource kinds beyond paths and command argvs. This reuses
  the entire existing ask/grant machinery unchanged: `Session._run_tool_calls()` dispatches
  through `on_permission_ask` exactly as it does for a directory or command item, and
  `PermissionAskPanel` shows a header ("Activate skill"), the skill's `description` as its
  detail, and the same once/session/workspace/homedir/deny grid described in [[permissions]].
  Persisting a scoped grant goes through a new `klorb.permissions.skill_grant` module
  (`apply_skill_permission_grant()`), mirroring `command_grant.py`'s own mirror of `grant.py` —
  see
  [the grant-writer generalization ADR](../../adrs/generalize-grant-writer-for-deny-and-mirror-it-for-commandrules.md).
  A persisted grant records the full `(namespace, name)` pair, never a bare name.
* Packaged skills expected to be safe by default (starting with `/create-edit-skill`) are
  pre-populated into `skillRules.allow` — as `["internal", "<name>"]` pairs — by
  `klorb.resources/default-config.json`, the same way that file pre-populates `readFiles.allow`
  for `/dev/null` — see [[permissions]]'s "File access" section. Because the pre-populated entry
  names the `internal` namespace explicitly, a workspace- or user-tier skill of the same name does
  not inherit its `allow`. The exact starting list is an implementation decision, not fixed by
  this spec.

### Supporting files: `ReadSkillFile`

A skill directory may bundle supporting files (reference material, templates, scripts) that its
own instructions point the model at. These live under the skill's directory, which is beneath
`.klorb/` or `$KLORB_DATA_DIR` — both hard-blocked from the general `ReadFile`/`EditFile` tools by
the `.klorb` self-tampering protection ([[permissions]]). Rather than punch a per-skill hole in
that hard gate (which would make "the file tools never see inside `.klorb`" a session-state-
dependent invariant, and would expose the whole skill subtree at once), a dedicated tool reads
them:

The set of `path` values a model would pass is exactly the manifest `ActivateSkill` already
returned, so no separate enumeration tool is needed.

* `ReadSkillFile(namespace: str, name: str, path: str)` resolves the skill's directory the same
  harness-resolved way `ActivateSkill` does — validated `(namespace, name)`, most-specific tier —
  then resolves `path` **as a safe relative path confined to that directory**: it must be
  relative (no leading `/` or `~`), contain no `..` component, and its symlink-resolved result
  must still be `is_relative_to` the skill directory (the same defense-in-depth
  canonicalize-then-containment check [[memories]] applies to a bare `filename`). It reads via
  `klorb.tools.util.ReadFileCore`, so it offers the same line-range mechanics as `ReadFile`. Like
  `SearchSkills`/`ActivateSkill`, it bypasses `readDirs`/the privileged-dirs hard gate
  structurally — because it only ever resolves `<resolved-skill-dir>/<validated-relative-path>`,
  never a model-supplied path into the rest of the filesystem — so the general file tools' hard
  gate stays absolute and unconditional.
* **Reading a supporting file is gated by the skill's own `skillRules` verdict, but raises no new
  ask beyond activation.** Reading a file the skill's instructions reference is strictly less than
  activating the skill itself, so `ReadSkillFile` requires the skill's `(namespace, name)` to
  evaluate to `"allow"` (or to have been granted this session via the activation ask); it does not
  raise a second, independent `PermissionAskRequired`. A `"deny"` skill's files are unreadable; an
  un-activated `"ask"` skill's files raise the same activation ask the model would hit on
  `ActivateSkill`.
* **Executing** a bundled script (as opposed to reading it) is out of scope here — that's a
  `BashTool`/sandbox question (a skill script lives inside `.klorb/`, which the sandbox binds; see
  the bind-workspace-`.klorb` ADR) and a separate follow-up.

### Filesystem access: bypassing `readDirs`, not participating in it

`SearchSkills`, `ActivateSkill`, and `ReadSkillFile` read `.klorb/skills/` and
`$KLORB_DATA_DIR/skills/` directly, the same way [[memories]]'s tools read `.klorb/memories/` and
`$KLORB_DATA_DIR/memories/`: a harness-resolved namespace directory plus a validated bare `name`
(and, for `ReadSkillFile`, a validated relative `path`), never a model-supplied path into the
rest of the filesystem, so there's nothing for `readDirs`/the `.klorb` self-tampering protection
(`directory_access.privileged_dirs()`, [[permissions]]) to usefully protect against — see
[the scratchpad-tools bypass ADR](../../adrs/scratchpad-tools-bypass-permission-tables.md) for
the precedent this follows. This is an entirely separate axis from `skillRules` above: a skill
being *readable* by these tools says nothing about whether `ActivateSkill` is *permitted* to hand
its content to the model — that's what `skillRules` alone decides.

### Creating and editing skills

There is no `CreateSkill`/`EditSkill` tool. A workspace- or user-tier skill is authored the same
way any other privileged-directory file is: `EscalatePrivileges(scope="workspace")` (for
`.klorb/skills/...`) or `EscalatePrivileges(scope="homedir")` (for `$KLORB_DATA_DIR/skills/...`)
— both scopes already cover these paths with no new scope needed, since `.klorb/skills/` is
beneath `.klorb/` and `$KLORB_DATA_DIR/skills/` is beneath `$KLORB_DATA_DIR`, both already
members of `directory_access.privileged_dirs()` — followed by ordinary
`CreateFile`/`EditFile`/`ReadFile` calls once approved (`docs/specs/tool-framework.md`). The
packaged tier (`klorb.resources.skills`) is never writable this way — it ships inside the
installed distribution, exactly like `system_prompts.d`'s packaged tier; a new built-in skill is
added to the klorb source tree, not authored by an agent at runtime.

Two authoring caveats the `/create-edit-skill` meta-skill must account for, because the
`EscalatePrivileges`-then-file-tools path behaves differently per tier:

* **Workspace tier is a single gate.** `EscalatePrivileges(scope="workspace")` only *lifts the
  hard `.klorb/` block* (it adds `"workspace"` to `SessionConfig.approved_scopes`, which
  `privileged_dirs()` consults to omit `.klorb/`); it does not itself write a `writeDirs` grant.
  But a *trusted* workspace's init already writes `writeDirs.allow: [workspace_root]` (see
  `klorb.workspace.workspace_init.write_initial_project_config`), and `.klorb/skills/` is beneath
  `workspace_root` — so once the hard block is lifted, the write is already allowed with no second
  ask. Since workspace-tier skills are only discoverable in a trusted workspace anyway, this is
  the common case.
* **User/homedir tier needs `EscalatePrivileges(homedir)` to also introduce session-level
  directory grants.** `$KLORB_DATA_DIR/skills/` is *outside* `workspace_root`, so lifting the
  `privileged_dirs()` block via `approved_scopes` is not enough on its own: for **reads**,
  trusted-mode `resolve_and_evaluate_read()` already skips the workspace boundary and would just
  need a `readDirs.allow` covering the data dir; for **writes**, `resolve_and_evaluate_write()`
  applies the hard `resolve_within_workspace()` boundary raise *before* `writeDirs` is ever
  consulted, and `approved_scopes` does not lift that boundary. So approving
  `EscalatePrivileges(scope="homedir")` must do two coordinated things beyond adding `"homedir"`
  to `approved_scopes`: (a) introduce **session-level** `readDirs.allow`/`writeDirs.allow` entries
  for the escalated dirs (`KLORB_DATA_DIR`, and per the scope's definition
  `KLORB_CONFIG_DIR`/`KLORB_STATE_DIR`) — in-memory only, revoked at session end, never persisted;
  and (b) lift the write-side `resolve_within_workspace()` boundary for paths within an approved
  escalation scope, symmetric to how trusted-read mode already skips that boundary. This is a
  change to `EscalatePrivileges`/`klorb.permissions.workspace` that the user-tier authoring path
  depends on, not something local to skills — it belongs in the permissions layer and should land
  there first. See
  [the homedir-escalation ADR](../../adrs/escalate-homedir-grants-session-dir-access-and-lifts-write-boundary.md).

Since this is a multi-step, convention-heavy dance (right directory, right frontmatter shape,
name-matches-directory discipline, remembering which `EscalatePrivileges` scope covers which
tier, the per-tier gate behavior above), the instructions for doing it live as klorb's own
packaged skill, `klorb.resources.skills/create-edit-skill/SKILL.md`, rather than in a dedicated
tool. This mirrors `docs/specs/klorb-init.md`'s own bootstrap-by-instructions approach and keeps
the tool surface small: "how to build a skill" is itself just a skill.

## Configuration

* `sessionDefaults.skillRules` — `{"deny": [...], "ask": [...], "allow": [...]}`, each a list of
  two-element `["<namespace>", "<name>"]` arrays (`namespace` one of `workspace`/`user`/
  `internal`), backing `SessionConfig.skill_rules`. Concatenated across config layers like
  `readDirs`/`writeDirs`/`commandRules` (see [[permissions]]'s "Configuration").
* No new `klorb-config.json` key controls *discovery* — the three tiers
  (`${workspace_root}/.klorb/skills/`, `$KLORB_DATA_DIR/skills/`, `klorb.resources.skills`) are
  fixed locations, scanned unconditionally (subject to the workspace-trust gate on the workspace
  tier), the same as `system_prompts.d`'s two fixed tiers.

## Known risks

* **A trusted workspace's own config layer can pre-`allow` its own workspace-tier skills.** Once a
  workspace is trusted (see [[projects-and-trust]]), its `.klorb/klorb-config.json` — the
  least-trusted config layer — is read, and its `skillRules.allow` entries concatenate into the
  same list every other layer's rules are evaluated against. So a trusted repository could ship
  both `.klorb/skills/foo/` and a `.klorb/klorb-config.json` granting `["workspace", "foo"]` in
  `skillRules.allow`, making `foo` activate with no ask. This is the exact same shape as the
  already-documented `readDirs.allow` known risk in [[permissions]], and is accepted for the same
  reason: reaching this state requires an explicit interactive decision to trust the workspace,
  which vouches for both its shipped skills and its config file. Keying grants on `(namespace,
  name)` (above) closes the strictly worse variant — a workspace skill *hijacking* a grant the
  user made for a same-named `internal`/`user` skill — leaving only "a repository you chose to
  trust can pre-allow its own skills," which is within the trust decision. Mitigation, as with
  `readDirs`: a user- or `/etc`-level `skillRules.deny` for a `(namespace, name)` always outranks
  any `allow`, since `deny` is evaluated first regardless of contributing layer.

## Out of scope

* **Vector-indexed skill search.** `SearchSkills` is a literal substring match; a future
  embedding-based index is real, separate follow-up work once the number of installed skills
  makes keyword search an insufficient filter.
* **Pruning the available-skills list.** Every non-denied skill's name/description is compiled
  into the first-turn interjection regardless of count; a future recency/frequency-based top-*k*
  cutoff is anticipated but not designed here.
* **Pinned tool results.** An activated skill's `SKILL.md` content lives only as an
  `ActivateSkill` tool-result message in history; under context summarization it can be compacted
  away mid-task with nothing re-injecting it, and the same is true of a `ReadSkillFile` result the
  model still needs. A general "pinned tool result" mechanism — a way to mark a tool result (an
  activated skill, a still-relevant reference file) as exempt from summarization/eviction so it
  survives for the duration of a task — is real follow-up work, not designed here; skills would be
  its first consumer.
* **Executing bundled skill scripts.** `ReadSkillFile` reads a skill's supporting files; running a
  bundled script via `BashTool` (its path lives inside `.klorb/`/`$KLORB_DATA_DIR`) is a separate
  sandbox/permissions question, not addressed here.
* **Glob/wildcard skill-name rules.** `SkillRules` matches exact `(namespace, name)` pairs only,
  unlike `CommandRules`' token patterns — a skill count large enough to want `("workspace",
  "eeweb-*"): allow`-style rules isn't the common case yet.
* **`compatibility.claudeSkills`.** `TODO.md` anticipates a compatibility toggle that additionally
  discovers `${workspace_root}/.claude/skills/` when enabled, the same shape
  `compatibility.claudeMarkdown` uses for `CLAUDE.md` (see [[workspace-context-files]]). Not
  designed here; a fourth discovery tier gated the same way is the expected shape when it is.
  Note also that Claude Code's own `SKILL.md` frontmatter may carry fields beyond `description`
  (a `name`, allowed-tools lists, model hints, ...) that this spec's minimal single-field
  frontmatter doesn't yet address — reconciling that is part of the same follow-up.
* **Role-scoped skill repertoires.** `Role.repertoire()` ([[roles-and-system-prompts]]) is a
  placeholder for a role constraining which skills it offers; this spec's skill list is
  role-agnostic — every session sees every discoverable, non-denied skill regardless of
  `SessionConfig.role_name`.
* **Subagent inheritance.** How a spawned subagent's skill discovery/activation state relates to
  its parent's (`TODO.md`'s "Subagent spawning" item) is unaddressed; today there is only one
  session, so the question doesn't yet arise.
* **Hot-reload guarantees.** The available-skills interjection is compiled once, at the first turn,
  and locked for the session; `SearchSkills`/`ActivateSkill`/`ReadSkillFile` rescan on demand. So
  there's no stale-cache risk within a tool call, but there's also no file-watching or explicit
  invalidation, and a skill added mid-session won't appear in the standing list until a `/clear`
  starts a fresh `Session`. This is "scan on demand / compile the standing list once," not "cache
  with invalidation."
