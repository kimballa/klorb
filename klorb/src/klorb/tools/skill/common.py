# © Copyright 2026 Aaron Kimball
"""Skill discovery, resolution, `SKILL.md` frontmatter parsing, `name`/`path` validation, and the
`skillRules` gate. See docs/specs/skills.md."""

import importlib.resources
import logging
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast

import yaml

from klorb.hooks.config import (
    EVENT_CONFIG_MODELS,
    HOOK_NAMES,
    PROCESS_SCOPED_HOOK_NAMES,
    EventConfig,
    HookConfig,
    TimerEventConfig,
)
from klorb.hooks.merge import parse_handler_list
from klorb.hooks.timer_events import clamp_timer_intervals
from klorb.paths import get_klorb_data_dir
from klorb.permissions.directory_access import (
    CLAUDE_PROJECT_DIR_NAME,
    KLORB_PROJECT_DIR_NAME,
    SKILLS_DIRNAME,
    canonicalize_dir,
)
from klorb.permissions.resource import PermissionOverride
from klorb.permissions.skill_access import VALID_NAMESPACES, Namespace, SkillId, SkillRules, evaluate_skill
from klorb.permissions.table import raise_if_not_allowed
from klorb.token_estimate import estimate_tokens

logger = logging.getLogger(__name__)

SKILL_FILE_NAME = "SKILL.md"
"""The one file every skill directory must contain to be discoverable; its basename directory is
the skill's `name` and its YAML frontmatter carries the skill's `description`."""

MAX_SKILL_NAME_DISPLAY_LENGTH = 64
"""Cap on a skill's canonical name — defends against a hostile, arbitrarily-long directory or
frontmatter-alias name bloating every turn's context. Applied once, at catalog-build time, to
*become* the skill's `(namespace, name)` identity itself, so a name once advertised to the model
or a user-facing skill list is always resolvable via `ActivateSkill`/`ReadSkillFile`."""

MAX_SKILL_DESCRIPTION_DISPLAY_LENGTH = 1024
"""Cap on a skill's description as advertised to the model. A frontmatter `description` is
project- or user-supplied free text with no length limit of its own; this cap is applied fresh
at each display site rather than baked into the catalog."""


def display_skill_name(name: str) -> str:
    """`name` truncated to `MAX_SKILL_NAME_DISPLAY_LENGTH`, with any trailing `-` truncation would
    leave behind stripped too — so the result always satisfies `is_valid_skill_name` when `name`
    itself did."""
    return name[:MAX_SKILL_NAME_DISPLAY_LENGTH].rstrip("-")


def display_skill_description(description: str) -> str:
    """`description` truncated to `MAX_SKILL_DESCRIPTION_DISPLAY_LENGTH`."""
    return description[:MAX_SKILL_DESCRIPTION_DISPLAY_LENGTH]


NAMESPACE_SCHEMA_PROPERTY: dict[str, object] = {
    "type": "string",
    "enum": list(VALID_NAMESPACES),
    "description": (
        "The skill's discovery tier: \"workspace\" (this project's .klorb/skills/, and "
        ".claude/skills/ when enabled), \"user\" (the per-user ~/.local/share/klorb/skills/), or "
        "\"internal\" (klorb's built-in skills)."
    ),
}
"""The `namespace` JSON-schema property for skill tool `parameters()`."""


@dataclass(frozen=True)
class ResolvedSkill:
    """A skill resolved to its `(namespace, name)` identity plus `root`, the `Traversable` its
    files live under (a real `Path` for the `workspace`/`user` tiers, and for the `internal` tier
    unless klorb is zip-installed)."""

    namespace: Namespace
    name: str
    root: Traversable


class SkillLocation(Protocol):
    """Structural type for anything with a resolved `(namespace, name)` identity and a `root`
    `Traversable` to read files from. Lets `read_skill_md`/`skill_file_manifest`/
    `resolve_skill_file` serve both a fresh `resolve_all_skills()` entry and a catalog-held
    `Skill` without duplicating the file-reading logic. Declared with read-only `@property`
    members because both implementers are frozen."""

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
    """Return `name` unchanged if it's a valid bare-slug skill name, else raise `ValueError`.
    Rejecting rather than normalizing keeps a model-supplied `name` from escaping its
    harness-resolved namespace directory."""
    if not isinstance(name, str) or not name:
        raise ValueError("skill name must be a non-empty string")
    if not is_valid_skill_name(name):
        raise ValueError(
            f"skill name must be a bare slug with no path separator, ':', or '..' component, no "
            f"leading/trailing '-', and no '<'/'>': {name!r}")
    return name


def is_valid_skill_name(name: str) -> bool:
    """Whether `name` is usable as a skill directory basename, a frontmatter alias, or a
    model-supplied `ActivateSkill`/`ReadSkillFile` argument: non-empty, no path separator or `:`,
    not `.`/`..`, no leading/trailing `-`, and no `<`/`>`."""
    return (
        bool(name) and "/" not in name and "\\" not in name and ":" not in name
        and name not in (".", "..")
        and not name.startswith("-") and not name.endswith("-")
        and "<" not in name and ">" not in name
    )


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Return a `SKILL.md`'s full YAML frontmatter as a raw `dict`, or `{}` if it has none or
    fails to parse.

    Parses the leading `---`-fenced YAML block with `yaml.safe_load` (never `yaml.load`). A
    missing block, malformed YAML, or a non-mapping document all yield `{}`, so a malformed skill
    is still discoverable with no frontmatter attributes.
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


def _skill_klorb_metadata(raw: dict[str, Any]) -> dict[str, Any] | None:
    """`raw`'s `metadata.klorb` frontmatter object, or `None` if `raw` has no `metadata` object
    or no `klorb` key within it. Shared drill-down for every `metadata.klorb.*` reader below."""
    metadata = raw.get("metadata")
    if not isinstance(metadata, dict):
        return None
    klorb_metadata = metadata.get("klorb")
    return klorb_metadata if isinstance(klorb_metadata, dict) else None


def skill_bash_command_patterns(raw: dict[str, Any]) -> list[list[str]]:
    """Every argv pattern under `raw`'s `metadata.klorb.bashCommands` frontmatter key, each a
    `list[str]` token pattern ready for `klorb.permissions.command_access.CommandRules.allow` —
    how a skill pre-authorizes the bash commands its own instructions need. A missing or malformed
    shape yields an empty list (or drops just that entry), logged as a `logger.warning()` since
    it's worth surfacing to whoever authored the skill.
    """
    klorb_metadata = _skill_klorb_metadata(raw)
    if klorb_metadata is None:
        return []
    entries = klorb_metadata.get("bashCommands")
    if entries is None:
        return []
    if not isinstance(entries, list):
        logger.warning("Skill metadata.klorb.bashCommands must be a list; got %r", entries)
        return []
    patterns: list[list[str]] = []
    for entry in entries:
        if isinstance(entry, list) and all(isinstance(token, str) for token in entry):
            patterns.append(list(entry))
        else:
            logger.warning(
                "Skill metadata.klorb.bashCommands entry skipped: not a list of strings: %r", entry)
    return patterns


def skill_hook_configs(raw: dict[str, Any]) -> dict[str, list[HookConfig]]:
    """Every hook entry under `raw`'s `metadata.klorb.hooks` frontmatter key, keyed by hook name,
    parsed via the same `klorb.hooks.merge.parse_handler_list` validation
    `load_process_config()` uses for the top-level `hooks` config key. A name outside
    `klorb.hooks.config.HOOK_NAMES`, or a `PROCESS_SCOPED_HOOK_NAMES` entry -- process-scoped
    hooks may only be configured via `klorb-config.json`'s own top-level `hooks` key, never a
    per-activation skill grant -- is dropped, logged as a `logger.warning()`. Every parsed
    `HookConfig` whose raw dict didn't set `isHeritable` gets `is_heritable=False` forced onto
    it here, overriding `HookConfig`'s own pydantic default of `True`: that default is right for
    a `klorb-config.json`-authored hook (meant to apply tree-wide unless it opts out), but wrong
    for a skill's own grant, which shouldn't silently widen to every subagent the activating
    session happens to create.
    """
    klorb_metadata = _skill_klorb_metadata(raw)
    if klorb_metadata is None:
        return {}
    raw_hooks = klorb_metadata.get("hooks")
    if raw_hooks is None:
        return {}
    if not isinstance(raw_hooks, dict):
        logger.warning("Skill metadata.klorb.hooks must be an object; got %r", raw_hooks)
        return {}
    result: dict[str, list[HookConfig]] = {}
    for name, raw_handlers in raw_hooks.items():
        if name not in HOOK_NAMES:
            logger.warning("Skill metadata.klorb.hooks names unrecognized hook %r; ignoring.", name)
            continue
        if name in PROCESS_SCOPED_HOOK_NAMES:
            logger.warning(
                "Skill metadata.klorb.hooks names process-scoped hook %r, which may only be "
                "configured via klorb-config.json's top-level hooks key; ignoring.", name)
            continue
        parsed, warnings = parse_handler_list(
            raw_handlers, model=HookConfig, source_label=f"skill metadata.klorb.hooks ({name})")
        for warning in warnings:
            logger.warning(warning)
        if parsed:
            result[name] = [
                handler if "is_heritable" in handler.model_fields_set
                else handler.model_copy(update={"is_heritable": False})
                for handler in parsed
            ]
    return result


def skill_event_configs(raw: dict[str, Any]) -> dict[str, list[EventConfig]]:
    """Every event entry under `raw`'s `metadata.klorb.events` frontmatter key, keyed by event
    name, parsed via `klorb.hooks.merge.parse_handler_list` against the `EventConfig` subclass
    `klorb.hooks.config.EVENT_CONFIG_MODELS` maps that name to -- mirrors `skill_hook_configs`,
    except no event name is ever process-scoped, so nothing is rejected on those grounds.
    `EventConfig.is_heritable` already defaults to `False` regardless of source, so (unlike
    `skill_hook_configs`) no override is needed here.
    """
    klorb_metadata = _skill_klorb_metadata(raw)
    if klorb_metadata is None:
        return {}
    raw_events = klorb_metadata.get("events")
    if raw_events is None:
        return {}
    if not isinstance(raw_events, dict):
        logger.warning("Skill metadata.klorb.events must be an object; got %r", raw_events)
        return {}
    result: dict[str, list[EventConfig]] = {}
    for name, raw_handlers in raw_events.items():
        model = EVENT_CONFIG_MODELS.get(name)
        if model is None:
            logger.warning("Skill metadata.klorb.events names unrecognized event %r; ignoring.", name)
            continue
        parsed, warnings = parse_handler_list(
            raw_handlers, model=model, source_label=f"skill metadata.klorb.events ({name})")
        for warning in warnings:
            logger.warning(warning)
        if name == "Timer":
            clamp_timer_intervals(
                cast("list[TimerEventConfig]", parsed),
                source_label=f"skill metadata.klorb.events ({name})", warnings=[])
        if parsed:
            result[name] = parsed
    return result


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
        return [get_klorb_data_dir() / SKILLS_DIRNAME]
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
    directories containing a `SKILL.md`. A directory that's otherwise a genuine skill candidate
    (has a `SKILL.md`) but fails `is_valid_skill_name` is skipped with a logged warning, since
    that's a content problem worth surfacing to whoever authored it -- a non-candidate child
    (no `SKILL.md`, or not a directory at all) is never warned about regardless of its name."""
    if not _is_dir(source):
        return []
    names: list[str] = []
    for child in source.iterdir():
        name = child.name
        if name.startswith("."):
            continue
        if not (_is_dir(child) and child.joinpath(SKILL_FILE_NAME).is_file()):
            continue
        if not is_valid_skill_name(name):
            logger.warning("Skill directory %r skipped: invalid name.", name)
            continue
        names.append(name)
    return sorted(names)


def resolve_all_skills(
    *, workspace_root: Path, workspace_trusted: bool, claude_skills_compat: bool,
) -> list[ResolvedSkill]:
    """Every discoverable skill, precedence-resolved and sorted by `name`. When the same `name`
    exists in more than one source, the most specific one wins and the rest are dropped. Not
    filtered by `skillRules`."""
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
    each path relative to that directory (including `SKILL.md`) — the `path` values a model then
    passes to `ReadSkillFile`. A symlink that escapes the skill directory is excluded."""
    root_real = canonicalize_dir(resolved.root, resolved.root) if isinstance(
        resolved.root, Path) else None
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
        verdict, resource_description=f"activate skill {namespace}:{name}{detail}", skill=skill_id)


def skill_activation_payload(skill: SkillLocation) -> dict[str, Any]:
    """Build the `{namespace, name, content, files, tokens}` payload for a resolved, gated
    skill — `skill`'s full `SKILL.md` content plus its file manifest. This is the single piece
    of code both `ActivateSkillTool.apply()` and `Session`'s `UserSkillActivation` interjection
    use to turn a `Skill` into what the model sees, so the two paths can never drift apart."""
    content = read_skill_md(skill)
    files = skill_file_manifest(skill)
    payload: dict[str, Any] = {
        "namespace": skill.namespace,
        "name": skill.name,
        "content": content,
        "files": files,
        "tokens": estimate_tokens(content) if content else 0,
    }
    if len(files):
        payload["file_access_hint"] = "Use ReadSkillFile to access paths in 'files' with " + \
            f"namespace=\"{skill.namespace}\", name=\"{skill.name}\""
    return payload
