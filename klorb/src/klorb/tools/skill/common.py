# © Copyright 2026 Aaron Kimball
"""Shared mechanics behind `SearchSkills`/`ActivateSkill`/`ReadSkillFile` and the session's
available-skills interjection: discovering skills across the three tiers (workspace/user/
internal), resolving a `(namespace, name)` pair to a skill directory, validating a model-supplied
`name`/`path`, parsing `SKILL.md` frontmatter, and enforcing the `skillRules` verdict. See
docs/specs/skills.md.

Deliberately imports neither `klorb.tools.setup_context` nor `klorb.session`: every function here
takes plain primitives (a workspace root `Path`, a trust `bool`, a `SkillRules`, ...) rather than
a `ToolSetupContext`, so `klorb.session` can call the discovery/verdict helpers directly to build
its `<AvailableSkills>`/`<SkillReference>` interjections without the import cycle the tool modules
themselves incur (they import `klorb.tools.setup_context`, which imports `klorb.session`). The
tool modules assemble these primitives from their own `ToolSetupContext` at call time.

Filesystem access here bypasses `readDirs`/the `.klorb` self-tampering hard gate structurally, the
same way `klorb.tools.memory` does: a harness-resolved namespace directory plus a validated bare
`name` (and, for a supporting file, a validated relative `path`), never a model-supplied path into
the rest of the filesystem -- see docs/adrs/scratchpad-tools-bypass-permission-tables.md for the
precedent. That is an entirely separate axis from `skillRules`, which alone decides whether
`ActivateSkill` may hand a skill's content to the model.
"""

import importlib.resources
import logging
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath

import yaml

from klorb.paths import KLORB_DATA_DIR
from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME
from klorb.permissions.skill_access import VALID_NAMESPACES, Namespace, SkillId, SkillRules, evaluate_skill
from klorb.permissions.table import PermissionOverride, raise_if_not_allowed

logger = logging.getLogger(__name__)

SKILLS_DIRNAME = "skills"
"""Directory name holding skill subdirectories within each tier's parent: `.klorb/skills/` and
(with `compatibility.claudeSkills`) `.claude/skills/` for `workspace`, `$KLORB_DATA_DIR/skills/`
for `user`, and `klorb.resources/skills/` for `internal`."""

SKILL_FILE_NAME = "SKILL.md"
"""The one file every skill directory must contain to be discoverable; its basename directory is
the skill's `name` and its YAML frontmatter carries the skill's `description`."""

CLAUDE_PROJECT_DIR_NAME = ".claude"
"""Workspace-root child directory whose `skills/` subtree is discovered as an additional
`workspace`-namespace source when `compatibility.claudeSkills` is enabled -- the same
Claude-Code-compatibility shape `.klorb/skills/` uses, and gated on the same workspace trust. On a
name collision, `.klorb/skills/` (klorb's own convention) wins over `.claude/skills/`."""

NAMESPACE_SCHEMA_PROPERTY: dict[str, object] = {
    "type": "string",
    "enum": list(VALID_NAMESPACES),
    "description": (
        "The skill's discovery tier: \"workspace\" (this project's .klorb/skills/, and "
        ".claude/skills/ when enabled), \"user\" (the per-user ~/.local/share/klorb/skills/), or "
        "\"internal\" (klorb's built-in skills)."
    ),
}
"""The `namespace` JSON-schema property shared by `ActivateSkill`/`ReadSkillFile`'s `parameters()`."""


@dataclass(frozen=True)
class ResolvedSkill:
    """A skill resolved to its on-disk (or packaged) directory: its `(namespace, name)` identity
    plus `root`, the `Traversable` its files live under (a real `Path` for the `workspace`/`user`
    tiers and for a normally directory-installed `internal` tier). Read `SKILL.md` via
    `read_skill_md`, enumerate supporting files via `skill_file_manifest`, and resolve one of them
    via `resolve_skill_file`."""

    namespace: Namespace
    name: str
    root: Traversable


@dataclass(frozen=True)
class DiscoveredSkill:
    """One entry in the available-skills list / a `SearchSkills` hit: a skill's `(namespace,
    name)` identity and its one-line `description` (empty when `SKILL.md` has no parseable
    `description` frontmatter)."""

    namespace: Namespace
    name: str
    description: str


def validate_namespace(namespace: object) -> Namespace:
    """Return `namespace` narrowed to a `Namespace`, or raise `ValueError` -- called by every
    skill tool on its model-supplied `namespace` argument before any disk access."""
    if namespace in VALID_NAMESPACES:
        return namespace  # type: ignore[return-value]
    raise ValueError(
        f"namespace must be one of {list(VALID_NAMESPACES)}, got {namespace!r}")


def validate_skill_name(name: object) -> str:
    """Return `name` unchanged if it's a valid bare-slug skill name, else raise `ValueError`.

    A valid name is a non-empty string with no path separator (`/` or `\\`) and no `..` component
    (since a name has no separators, that means it must not itself be `.` or `..`). Rejected --
    rather than silently normalized -- so a model-supplied `name` can never be steered into a path
    that escapes its harness-resolved namespace directory, the same discipline
    `klorb.tools.memory.common.validate_memory_filename` enforces. Called by `ActivateSkill`/
    `ReadSkillFile` on their `name` argument; a directory whose basename fails the equivalent
    `is_valid_skill_name` check is skipped during discovery rather than raising.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("skill name must be a non-empty string")
    if not is_valid_skill_name(name):
        raise ValueError(
            f"skill name must be a bare slug with no path separator or '..' component: {name!r}")
    return name


def is_valid_skill_name(name: str) -> bool:
    """Whether `name` is usable as a skill directory basename: non-empty, no path separator, and
    not `.`/`..`. The predicate form used during discovery (skip, don't raise); `validate_skill_name`
    is the raising form for a model-supplied tool argument."""
    return bool(name) and "/" not in name and "\\" not in name and name not in (".", "..")


def parse_frontmatter_description(text: str) -> str:
    """Return a `SKILL.md`'s `description` frontmatter field, or `""` if it has none.

    Parses the leading `---`-fenced YAML block with `yaml.safe_load` (never `yaml.load`: a
    workspace-tier skill's frontmatter is project-supplied content, and `safe_load` refuses
    `!!python/object`-style tags, so parsing it can never construct arbitrary objects or execute
    code). A missing block, malformed YAML, a non-mapping document, or a missing/non-string
    `description` all yield `""` -- never an exception -- so a malformed skill is still
    discoverable (it exists on disk); it simply contributes an empty description.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    closing_index = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if closing_index is None:
        return ""
    block = "\n".join(lines[1:closing_index])
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return ""
    if not isinstance(data, dict):
        return ""
    description = data.get("description")
    if not isinstance(description, str):
        return ""
    return description.strip()


def _namespace_source_dirs(
    namespace: Namespace, workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
) -> list[Traversable]:
    """The ordered source directories a `namespace`'s skills are discovered from, most specific
    first. The `workspace` namespace contributes nothing when the workspace is untrusted (the same
    gate `klorb.tools.memory` and docs/specs/workspace-context-files.md apply), and additionally
    contributes `.claude/skills/` after `.klorb/skills/` when `claude_skills_compat` is set."""
    if namespace == "workspace":
        if not workspace_trusted:
            return []
        dirs: list[Traversable] = [workspace_root / KLORB_PROJECT_DIR_NAME / SKILLS_DIRNAME]
        if claude_skills_compat:
            dirs.append(workspace_root / CLAUDE_PROJECT_DIR_NAME / SKILLS_DIRNAME)
        return dirs
    if namespace == "user":
        return [KLORB_DATA_DIR / SKILLS_DIRNAME]
    return [internal_skills_dir()]


def internal_skills_dir() -> Traversable:
    """The packaged `internal`-tier skills root, `klorb.resources/skills/`, read via
    `importlib.resources` — the same mechanism `system_prompts.d`'s packaged tier uses. Factored
    out as its own function so tests can redirect the internal tier at a temp dir (the packaged
    `create-edit-skill` skill is otherwise discoverable in every session)."""
    return importlib.resources.files("klorb.resources").joinpath(SKILLS_DIRNAME)


def _tier_source_dirs(
    workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
) -> list[tuple[Namespace, Traversable]]:
    """Every `(namespace, source_dir)` pair, across all three tiers, in most- to least-specific
    precedence order (workspace `.klorb`, workspace `.claude`, user, internal) -- the order in
    which a name shadows a lower tier's same name."""
    tiers: list[tuple[Namespace, Traversable]] = []
    for namespace in VALID_NAMESPACES:
        for source in _namespace_source_dirs(
                namespace, workspace_root, workspace_trusted, claude_skills_compat):
            tiers.append((namespace, source))
    return tiers


def _is_dir(node: Traversable) -> bool:
    """Whether `node` exists and is a directory -- tolerating a non-existent packaged/real path
    (`is_dir()` returns `False` rather than raising for those)."""
    try:
        return node.is_dir()
    except OSError:
        return False


def _skill_dir_names(source: Traversable) -> list[str]:
    """The sorted basenames of `source`'s immediate children that are valid, non-hidden skill
    directories containing a `SKILL.md`. A directory with no `SKILL.md` is ignored entirely (not
    an error, just not a skill)."""
    if not _is_dir(source):
        return []
    names: list[str] = []
    for child in source.iterdir():
        name = child.name
        if name.startswith(".") or not is_valid_skill_name(name):
            continue
        if _is_dir(child) and child.joinpath(SKILL_FILE_NAME).is_file():
            names.append(name)
    return sorted(names)


def resolve_all_skills(
    *, workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
) -> list[ResolvedSkill]:
    """Every discoverable skill, precedence-resolved and sorted by `name`. When the same `name`
    exists in more than one tier (or in both workspace source dirs), the most specific tier wins
    outright and the others' copies of that name are not consulted at all -- the same all-or-
    nothing shadowing `resolve_prompt_file()` uses. Not filtered by `skillRules`: callers that
    need to exclude denied skills apply `evaluate_skill` themselves (see `discover_skills`)."""
    resolved: dict[str, ResolvedSkill] = {}
    for namespace, source in _tier_source_dirs(
            workspace_root, workspace_trusted, claude_skills_compat):
        for name in _skill_dir_names(source):
            if name in resolved:
                continue  # shadowed by a more specific tier / earlier source dir
            resolved[name] = ResolvedSkill(
                namespace=namespace, name=name, root=source.joinpath(name))
    return [resolved[name] for name in sorted(resolved)]


def discover_skills(
    *, workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
    skill_rules: SkillRules,
) -> list[DiscoveredSkill]:
    """Every discoverable, non-`deny`-verdicted skill as a `DiscoveredSkill` (identity plus
    one-line description), sorted by `name` -- what the available-skills interjection lists and
    what `SearchSkills` narrows. A skill whose `(namespace, name)` evaluates to `"deny"` is
    excluded entirely: there's no reason to advertise a skill the model structurally cannot
    activate."""
    out: list[DiscoveredSkill] = []
    for resolved in resolve_all_skills(
            workspace_root=workspace_root, workspace_trusted=workspace_trusted,
            claude_skills_compat=claude_skills_compat):
        if evaluate_skill(skill_rules, (resolved.namespace, resolved.name)) == "deny":
            continue
        out.append(DiscoveredSkill(
            namespace=resolved.namespace, name=resolved.name,
            description=read_skill_description(resolved)))
    return out


def resolve_skill(
    *, workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
    namespace: Namespace, name: str,
) -> ResolvedSkill | None:
    """Resolve an exact `(namespace, name)` pair to its `ResolvedSkill`, or `None` if that tier
    has no such skill (including when `namespace` is `workspace` but the workspace is untrusted, so
    the whole tier is skipped). Identity is the full pair, so this resolves within `namespace`'s
    own source dir(s) -- never a more specific tier's same-named skill. `name` must already be
    validated by the caller (see `validate_skill_name`)."""
    for source in _namespace_source_dirs(
            namespace, workspace_root, workspace_trusted, claude_skills_compat):
        candidate = source.joinpath(name)
        if _is_dir(candidate) and candidate.joinpath(SKILL_FILE_NAME).is_file():
            return ResolvedSkill(namespace=namespace, name=name, root=candidate)
    return None


def read_skill_md(resolved: ResolvedSkill) -> str:
    """Return `resolved`'s full `SKILL.md` content."""
    return resolved.root.joinpath(SKILL_FILE_NAME).read_text(encoding="utf-8")


def read_skill_description(resolved: ResolvedSkill) -> str:
    """Return `resolved`'s one-line `description` frontmatter, or `""` on any read/parse problem
    (a discoverable-but-malformed skill still contributes an empty description, never a failure)."""
    try:
        text = read_skill_md(resolved)
    except (OSError, UnicodeDecodeError):
        return ""
    return parse_frontmatter_description(text)


def _iter_relative_files(node: Traversable, prefix: str) -> list[str]:
    """Recursively enumerate every regular file beneath `node`, returned as `/`-separated paths
    relative to the original root (`prefix` accumulates the path walked so far)."""
    files: list[str] = []
    for child in node.iterdir():
        relative = f"{prefix}{child.name}"
        if child.is_dir():
            files.extend(_iter_relative_files(child, f"{relative}/"))
        elif child.is_file():
            files.append(relative)
    return files


def skill_file_manifest(resolved: ResolvedSkill) -> list[str]:
    """A sorted `find -type f`-style manifest of every regular file beneath `resolved`'s
    directory, each path relative to that directory (including `SKILL.md` itself) -- the exact
    `path` values a model then passes to `ReadSkillFile`."""
    return sorted(_iter_relative_files(resolved.root, ""))


def resolve_skill_file(resolved: ResolvedSkill, path: str) -> Traversable:
    """Resolve a supporting-file `path` to the `Traversable` it names, confined to `resolved`'s
    directory.

    `path` must be relative (no leading `/` or `~`) and contain no `..` component. Raises
    `ValueError` for a malformed/escaping `path` and `FileNotFoundError` if nothing is there. The
    returned `Traversable` is read via `klorb.tools.util.ReadFileCore.apply_readable`, so
    `ReadSkillFile` offers the same line-range mechanics as `ReadFile` whether the file lives on the
    real filesystem (`workspace`/`user` tiers) or inside the packaged distribution (the `internal`
    tier, which a zip/wheel install reaches only through the resource loader, not `open()`).

    For a real-filesystem root, a defense-in-depth symlink-canonicalization containment check is
    applied on top of the string validation (a bundled symlink could otherwise point outside the
    skill directory); a packaged `Traversable` root has no symlinks to canonicalize, so the string
    validation alone confines it.
    """
    if not path:
        raise ValueError("path must be a non-empty relative path")
    if path.startswith("~"):
        raise ValueError(f"path must not start with '~': {path!r}")
    pure = PurePosixPath(path)
    if pure.is_absolute():
        raise ValueError(f"path must be relative, not absolute: {path!r}")
    parts = [part for part in pure.parts if part != "."]
    if any(part == ".." for part in parts):
        raise ValueError(f"path must not contain a '..' component: {path!r}")

    target: Traversable = resolved.root
    for part in parts:
        target = target.joinpath(part)

    if isinstance(resolved.root, Path):
        assert isinstance(target, Path)
        root_real = resolved.root.resolve(strict=False)
        target_real = target.resolve(strict=False)
        if not (target_real == root_real or target_real.is_relative_to(root_real)):
            raise ValueError(f"path escapes the skill directory: {path!r}")
        target = target_real

    if not target.is_file():
        raise FileNotFoundError(f"no such file in skill {resolved.namespace}/{resolved.name}: {path}")
    return target


def resolve_and_gate_skill(
    *, workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
    skill_rules: SkillRules, override: PermissionOverride | None,
    namespace: object, name: object,
) -> ResolvedSkill:
    """Validate `namespace`/`name`, resolve the `(namespace, name)` pair to a `ResolvedSkill`, and
    enforce its `skillRules` verdict -- the shared front half of both `ActivateSkill` and
    `ReadSkillFile`. Raises `ValueError` for a malformed argument or an unknown pair (no permission
    question raised for a not-found skill), `PermissionError`/`PermissionAskRequired` for a
    `"deny"`/`"ask"` verdict (see `raise_if_skill_not_allowed`)."""
    validated_namespace = validate_namespace(namespace)
    validated_name = validate_skill_name(name)
    resolved = resolve_skill(
        workspace_root=workspace_root, workspace_trusted=workspace_trusted,
        claude_skills_compat=claude_skills_compat,
        namespace=validated_namespace, name=validated_name)
    if resolved is None:
        raise ValueError(f"no such skill: {validated_namespace}/{validated_name}")
    raise_if_skill_not_allowed(
        skill_rules, override, validated_namespace, validated_name,
        description=read_skill_description(resolved))
    return resolved


def raise_if_skill_not_allowed(
    skill_rules: SkillRules, override: PermissionOverride | None,
    namespace: Namespace, name: str, *, description: str,
) -> None:
    """Enforce a skill's `skillRules` verdict before its content is read: return normally on
    `"allow"` (or when a one-shot `override` already covers this `(namespace, name)`), raise
    `PermissionError` on `"deny"`, and raise `PermissionAskRequired` (carrying the skill identity)
    on `"ask"`. Shared by `ActivateSkill` and `ReadSkillFile` so reading a supporting file raises
    no second, independent ask beyond activation -- both gate on the same verdict."""
    skill_id: SkillId = (namespace, name)
    verdict = evaluate_skill(skill_rules, skill_id)
    if verdict == "allow":
        return
    if override is not None and skill_id in override.skills:
        return
    detail = f": {description}" if description else ""
    raise_if_not_allowed(
        verdict, resource_description=f"activate skill {namespace}/{name}{detail}", skill=skill_id)
