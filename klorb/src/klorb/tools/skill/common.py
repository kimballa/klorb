# © Copyright 2026 Aaron Kimball
"""Skill discovery, resolution, `SKILL.md` frontmatter parsing, `name`/`path` validation, and the
`skillRules` gate, shared by `SearchSkills`/`ActivateSkill`/`ReadSkillFile` and the session's
skill interjections. See docs/specs/skills.md.

Every function takes plain primitives rather than a `ToolSetupContext`, so `klorb.session` can call
the discovery helpers without an import cycle (the tool modules assemble the primitives from their
own context). Filesystem access bypasses the `readDirs`/`.klorb` hard gate structurally, per
docs/adrs/scratchpad-tools-bypass-permission-tables.md.
"""

import importlib.resources
import logging
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import yaml

from klorb.paths import KLORB_DATA_DIR
from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME, canonicalize_dir
from klorb.permissions.skill_access import VALID_NAMESPACES, Namespace, SkillId, SkillRules, evaluate_skill
from klorb.permissions.table import PermissionOverride, raise_if_not_allowed
from klorb.token_estimate import estimate_tokens

logger = logging.getLogger(__name__)

SKILLS_DIRNAME = "skills"
"""Directory name holding skill subdirectories within each tier's parent: `.klorb/skills/` and
(with `compatibility.claudeSkills`) `.claude/skills/` for `workspace`, `$KLORB_DATA_DIR/skills/`
for `user`, and `klorb.resources/skills/` for `internal`."""

SKILL_FILE_NAME = "SKILL.md"
"""The one file every skill directory must contain to be discoverable; its basename directory is
the skill's `name` and its YAML frontmatter carries the skill's `description`."""

CLAUDE_PROJECT_DIR_NAME = ".claude"
"""Workspace-root child directory whose `skills/` subtree is discovered as a second
`workspace`-namespace source when `compatibility.claudeSkills` is enabled -- see docs/specs/skills.md."""

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
    """A skill resolved to its `(namespace, name)` identity plus `root`, the `Traversable` its
    files live under (a real `Path` for the `workspace`/`user` tiers, and for the `internal` tier
    unless klorb is zip-installed)."""

    namespace: Namespace
    name: str
    root: Traversable


class SkillLocation(Protocol):
    """Structural type shared by `ResolvedSkill` and `klorb.tools.skill.model.Skill`: anything
    with a resolved `(namespace, name)` identity and a `root` `Traversable` to read files from.
    Lets `read_skill_md`/`skill_file_manifest`/`resolve_skill_file` serve both a fresh
    `resolve_all_skills()` entry and a catalog-held `Skill` without duplicating the file-reading
    logic. Declared with read-only `@property` members (rather than plain attributes) because
    both implementers are frozen -- a frozen dataclass's/pydantic model's fields are read-only
    from mypy's perspective, and a plain-attribute Protocol member requires a settable one."""

    @property
    def namespace(self) -> Namespace: ...

    @property
    def name(self) -> str: ...

    @property
    def root(self) -> Traversable: ...


def validate_namespace(namespace: object) -> Namespace:
    """Return `namespace` narrowed to a `Namespace`, or raise `ValueError`."""
    if namespace in VALID_NAMESPACES:
        return namespace  # type: ignore[return-value]
    raise ValueError(
        f"namespace must be one of {list(VALID_NAMESPACES)}, got {namespace!r}")


def validate_skill_name(name: object) -> str:
    """Return `name` unchanged if it's a valid bare-slug skill name (see `is_valid_skill_name`),
    else raise `ValueError`. Rejecting rather than normalizing keeps a model-supplied `name` from
    escaping its harness-resolved namespace directory."""
    if not isinstance(name, str) or not name:
        raise ValueError("skill name must be a non-empty string")
    if not is_valid_skill_name(name):
        raise ValueError(
            f"skill name must be a bare slug with no path separator, ':', or '..' component: "
            f"{name!r}")
    return name


def is_valid_skill_name(name: str) -> bool:
    """Whether `name` is usable as a skill directory basename: non-empty, no path separator or
    `:` (the fully-qualified-skill-name separator -- see `klorb.permissions.skill_access.
    format_fqsn`), and not `.`/`..`."""
    return (
        bool(name) and "/" not in name and "\\" not in name and ":" not in name
        and name not in (".", "..")
    )


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Return a `SKILL.md`'s full YAML frontmatter as a raw `dict`, or `{}` if it has none or
    fails to parse.

    Parses the leading `---`-fenced YAML block with `yaml.safe_load` (never `yaml.load` -- the
    frontmatter is project-supplied content). A missing block, malformed YAML, or a non-mapping
    document all yield `{}`, so a malformed skill is still discoverable with no frontmatter
    attributes. This is the source `klorb.tools.skill.model.Skill.raw`/`.description`/the
    frontmatter `name` alias are all read from -- see `klorb.tools.skill.catalog`.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    closing_index = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if closing_index is None:
        return {}
    block = "\n".join(lines[1:closing_index])
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _namespace_source_dirs(
    namespace: Namespace, workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
) -> list[Traversable]:
    """The ordered source directories a `namespace`'s skills are discovered from, most specific
    first. The `workspace` namespace contributes nothing when the workspace is untrusted, and adds
    `.claude/skills/` after `.klorb/skills/` when `claude_skills_compat` is set."""
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
    `importlib.resources`. Its own function so tests can redirect the internal tier."""
    return importlib.resources.files("klorb.resources").joinpath(SKILLS_DIRNAME)


def _tier_source_dirs(
    workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
) -> list[tuple[Namespace, Traversable]]:
    """Every `(namespace, source_dir)` pair across all tiers, in most- to least-specific precedence
    order (workspace `.klorb`, workspace `.claude`, user, internal)."""
    tiers: list[tuple[Namespace, Traversable]] = []
    for namespace in VALID_NAMESPACES:
        for source in _namespace_source_dirs(
                namespace, workspace_root, workspace_trusted, claude_skills_compat):
            tiers.append((namespace, source))
    return tiers


def _is_dir(node: Traversable) -> bool:
    """Whether `node` exists and is a directory, tolerating a non-existent path."""
    try:
        return node.is_dir()
    except OSError:
        return False


def _skill_dir_names(source: Traversable) -> list[str]:
    """The sorted basenames of `source`'s immediate children that are valid, non-hidden skill
    directories containing a `SKILL.md`."""
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
    exists in more than one source, the most specific one wins and the rest are dropped. Not
    filtered by `skillRules` -- see `klorb.tools.skill.catalog.SkillCatalog.discoverable` for
    that."""
    resolved: dict[str, ResolvedSkill] = {}
    for namespace, source in _tier_source_dirs(
            workspace_root, workspace_trusted, claude_skills_compat):
        for name in _skill_dir_names(source):
            if name in resolved:
                continue
            resolved[name] = ResolvedSkill(
                namespace=namespace, name=name, root=source.joinpath(name))
    return [resolved[name] for name in sorted(resolved)]


def read_skill_md(resolved: SkillLocation) -> str:
    """Return `resolved`'s full `SKILL.md` content."""
    return resolved.root.joinpath(SKILL_FILE_NAME).read_text(encoding="utf-8")


def _iter_relative_files(node: Traversable, prefix: str, root_real: Path | None) -> list[str]:
    """Recursively enumerate every regular file beneath `node`, returned as `/`-separated paths
    relative to the original root (`prefix` accumulates the path walked so far). When `root_real`
    is set (a real-filesystem root), each child is canonicalized and confined to `root_real`
    before being recursed into or listed -- the same containment boundary `resolve_skill_file`
    enforces for an actual read -- so a symlink pointing outside the skill directory is skipped
    rather than followed and leaked into the manifest."""
    files: list[str] = []
    for child in node.iterdir():
        if root_real is not None:
            assert isinstance(child, Path)
            child_real = canonicalize_dir(child, root_real)
            if not (child_real == root_real or child_real.is_relative_to(root_real)):
                continue
        relative = f"{prefix}{child.name}"
        if child.is_dir():
            files.extend(_iter_relative_files(child, f"{relative}/", root_real))
        elif child.is_file():
            files.append(relative)
    return files


def skill_file_manifest(resolved: SkillLocation) -> list[str]:
    """A sorted `find -type f`-style manifest of every regular file beneath `resolved`'s directory,
    each path relative to that directory (including `SKILL.md`) -- the `path` values a model then
    passes to `ReadSkillFile`. A symlink that escapes the skill directory is excluded, the same
    containment boundary `resolve_skill_file` enforces for an actual read."""
    root_real = canonicalize_dir(resolved.root, resolved.root) if isinstance(resolved.root, Path) else None
    return sorted(_iter_relative_files(resolved.root, "", root_real))


def resolve_skill_file(resolved: SkillLocation, path: str) -> Traversable:
    """Resolve a supporting-file `path` to the `Traversable` it names, confined to `resolved`'s
    directory. `path` must be relative (no leading `/` or `~`) and contain no `..` component;
    raises `ValueError` for a malformed/escaping `path` and `FileNotFoundError` if nothing is
    there.

    For a real-filesystem root, a symlink-canonicalization containment check backs up the string
    validation; a packaged `Traversable` root has no symlinks, so the string checks confine it.
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
        root_real = canonicalize_dir(resolved.root, resolved.root)
        target_real = canonicalize_dir(target, resolved.root)
        if not (target_real == root_real or target_real.is_relative_to(root_real)):
            raise ValueError(f"path escapes the skill directory: {path!r}")
        target = target_real

    if not target.is_file():
        raise FileNotFoundError(f"no such file in skill {resolved.namespace}/{resolved.name}: {path}")
    return target


def raise_if_skill_not_allowed(
    skill_rules: SkillRules, override: PermissionOverride | None,
    namespace: Namespace, name: str, *, description: str,
) -> None:
    """Enforce a skill's `skillRules` verdict before its content is read: return on `"allow"` (or
    when a one-shot `override` covers this `(namespace, name)` and the verdict is `"ask"`), raise
    `PermissionError` on `"deny"`, and raise `PermissionAskRequired` (carrying the skill identity)
    on `"ask"`.

    `override` is only ever consulted for an `"ask"` verdict -- never a `"deny"` one -- so a
    one-shot bypass can retry a skill the user was just asked about, but can never resurrect a
    skill the table itself denies, even if a future caller passed a stale/reused `override`."""
    skill_id: SkillId = (namespace, name)
    verdict = evaluate_skill(skill_rules, skill_id)
    if verdict == "allow":
        return
    if verdict == "ask" and override is not None and skill_id in override.skills:
        return
    detail = f": {description}" if description else ""
    raise_if_not_allowed(
        verdict, resource_description=f"activate skill {namespace}/{name}{detail}", skill=skill_id)


def skill_activation_payload(skill: SkillLocation) -> dict[str, Any]:
    """Build the `{namespace, name, content, files, tokens}` payload for a resolved, gated
    skill -- `skill`'s full `SKILL.md` content plus its file manifest. This is the single piece
    of code both `ActivateSkillTool.apply()` and `Session`'s `UserSkillActivation` interjection
    (for a user prompt that *starts* with a skill reference, see docs/specs/skills.md) use to
    turn a `Skill` into what the model sees, so the two paths can never drift apart."""
    content = read_skill_md(skill)
    files = skill_file_manifest(skill)
    return {
        "namespace": skill.namespace,
        "name": skill.name,
        "content": content,
        "files": files,
        "tokens": estimate_tokens(content) if content else 0,
    }
