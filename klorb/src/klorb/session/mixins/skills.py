# © Copyright 2026 Aaron Kimball
"""`SessionSkillsMixin`: workspace context-file discovery and every skill-related
`<SystemInterjection>` body `send_turn()` may prepend onto a turn's prompt -- the standing
`AvailableSkills` catalog, the per-turn `SkillReference` reminder, and the leading-mention
`UserSkillActivation` shortcut. See docs/specs/skills.md."""

import json
import logging

from klorb.paths import get_klorb_config_dir
from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME
from klorb.permissions.skill_access import evaluate_skill
from klorb.session.events import UserSkillActivation
from klorb.session.mixins._base import SessionBase
from klorb.tools.skill.catalog import SkillCatalogs
from klorb.tools.skill.common import (
    display_skill_description,
    skill_activation_payload,
    skill_bash_command_patterns,
)
from klorb.tools.skill.model import Skill

logger = logging.getLogger(__name__)


class SessionSkillsMixin(SessionBase):
    """Workspace context-file (`AGENTS.md`/`CLAUDE.md`/`.klorb/INSTRUCTIONS.md`) discovery and
    the skill-catalog-backed interjection builders `send_turn()` calls into."""

    def _build_context_files_interjection(self) -> str | None:
        """Return the body `send_turn()` wraps in a `<SystemInterjection subject=
        "ProjectGuidance">` tag and prepends onto the very first turn's prompt, or `None` if
        there's nothing to say.

        If `config.workspace.trusted` is `False`: `.klorb/INSTRUCTIONS.md`, `AGENTS.md`, and
        `CLAUDE.md` are ignored. As project-supplied content, a hostile, downloaded-and-unzipped
        repository could ship any of them to smuggle instructions into the model's context the
        moment the user runs klorb from inside it — the same risk `.klorb/klorb-config.json`'s
        own trust gate exists to close (see docs/specs/projects-and-trust.md). So none of them
        are ever read into the prompt until the user has explicitly trusted the workspace,
        exactly like that config layer.

        Otherwise reads each of `_applicable_context_filenames()` (in priority order) that
        exists on disk, relative to `config.workspace.path`, and wraps each one's contents in
        a `<ContextFile filename="..." priority="N">` tag, `N` starting at `1` in that same
        priority order — giving the model an explicit signal for which file should win if two
        ever conflict.

        Additionally checks for `INSTRUCTIONS.md` in `KLORB_CONFIG_DIR` and includes it with highest
        priority (priority 1) when present. This file is included in the system instructions even in
        an untrusted workspace.
        """

        context_files: list[tuple[str, str]] = []

        # Check for INSTRUCTIONS.md in KLORB_CONFIG_DIR (highest priority)
        config_instructions_path = get_klorb_config_dir() / "INSTRUCTIONS.md"
        if config_instructions_path.is_file():
            context_files.append(("KLORB_CONFIG_DIR/INSTRUCTIONS.md", config_instructions_path.read_text()))

        if self.config.workspace.trusted:
            # Check workspace root files
            for filename in self._applicable_context_filenames():
                path = self.config.workspace.path / filename
                if path.is_file():
                    context_files.append((filename, path.read_text()))

        if not context_files:
            return None

        sections = [
            f'<ContextFile filename="{filename}" priority="{index + 1}">\n{contents}\n'
            f'</ContextFile>'
            for index, (filename, contents) in enumerate(context_files)
        ]
        return (
            "This workspace contains one or more files with instructions and context for "
            "working in this repository. Treat them as standing guidance about the "
            "project's conventions and requirements, not a task to act on directly.\n\n" +
            "\n\n".join(sections)
        )

    def _applicable_context_filenames(self) -> list[str]:
        """Return the ordered list of context-instruction filenames to read, relative to the
        workspace root, most authoritative first: `.klorb/INSTRUCTIONS.md` (priority 1 —
        durable per-project instructions kept alongside `klorb-config.json` rather than at the
        workspace root), then `AGENTS.md` (priority 2), then `CLAUDE.md`
        (priority 3) when `_compatibility_claude_markdown` is enabled."""
        filenames = [f"{KLORB_PROJECT_DIR_NAME}/INSTRUCTIONS.md", "AGENTS.md"]
        if self._compatibility_claude_markdown:
            filenames.append("CLAUDE.md")
        return filenames

    def _ensure_skill_catalog(self) -> None:
        """Build this session's skill catalog if it hasn't built one yet -- a no-op after the
        first call. See `klorb.tools.skill.catalog.SkillCatalogRegistry.ensure`."""
        self._skill_catalog_registry.ensure(
            workspace_root=self.config.workspace.path,
            workspace_trusted=self.config.workspace.trusted,
            claude_skills_compat=self._compatibility_claude_skills,
            skill_rules=self.config.skill_rules,
        )

    def reload_skills(self) -> SkillCatalogs:
        """Rebuild this session's skill catalog from a fresh disk scan against the current
        workspace -- the ">Reload skills" command palette action and `_klorb/reloadSkills` ACP
        extension's shared implementation (see `klorb.tui.commands.skill_commands` and
        docs/specs/klorb-server.md), and also called whenever a workspace's trust state changes
        (a newly-trusted workspace's `.klorb/skills/` tier is otherwise invisible until an
        explicit reload). Prefers the live `ProcessConfig.compatibility_claude_skills` this
        session was constructed with over its own construction-time snapshot
        (`_compatibility_claude_skills`), since a caller holding a `ProcessConfig` reference may
        have just reloaded it (e.g. a newly-trusted workspace's own config layer)."""
        claude_skills_compat = (
            self._process_config.compatibility_claude_skills if self._process_config is not None
            else self._compatibility_claude_skills
        )
        return self._skill_catalog_registry.reload(
            workspace_root=self.config.workspace.path,
            workspace_trusted=self.config.workspace.trusted,
            claude_skills_compat=claude_skills_compat,
            skill_rules=self.config.skill_rules,
        )

    def discover_skills(self) -> list[Skill]:
        """Every currently non-`deny`-verdicted skill, precedence-deduped by name, from this
        session's catalog and its live `skill_rules`. No disk access -- see
        `klorb.tools.skill.catalog.SkillCatalog.discoverable` and docs/specs/skills.md."""
        self._ensure_skill_catalog()
        return self._skill_catalog_registry.canonical().discoverable(self.config.skill_rules)

    @staticmethod
    def _format_skill_list(skills: list[Skill]) -> str:
        """Render `skills` as the newline-joined `- <name> (<namespace>): <description>` bullet
        list shared by both skill interjections, always by canonical name (a skill's directory
        basename, already lowercased and length-capped -- see `klorb.tools.skill.catalog.
        build_catalogs`), never a frontmatter-name alias. A skill with an empty description
        contributes just `- <name> (<namespace>)`. `description` is additionally capped
        (`display_skill_description`) before display, since it's arbitrary frontmatter text with
        no length limit of its own."""
        lines = []
        for skill in skills:
            description = display_skill_description(skill.description)
            lines.append(f"- {skill.name} ({skill.namespace}): {description}" if description
                         else f"- {skill.name} ({skill.namespace})")
        return "\n".join(lines)

    def _build_available_skills_interjection(self, skills: list[Skill]) -> str | None:
        """Return the body `send_turn()` wraps in an `AvailableSkills` `<SystemInterjection>` and
        prepends onto the first turn's prompt, or `None` if no skill is discoverable. Lists every
        discoverable, non-`deny`-verdicted skill. Built once and locked for the session — see
        `_skills_seeded` and docs/specs/skills.md. `skills` is whatever `send_turn()` already
        discovered for this turn -- see that method for why it isn't rediscovered here."""
        if not skills:
            return None
        return (
            "The following skills are available. A skill is a set of instructions for one "
            "bounded, reusable task. When one is relevant to what you're doing, load its full "
            "instructions with `ActivateSkill(namespace=\"<namespace>\", name=\"<name>\")` and "
            "follow them. Use `SearchSkills()` to narrow this list by keyword.\n"
            + self._format_skill_list(skills)
        )

    def _build_skill_reference_interjection(
        self, tokens: list[str], *, exclude: frozenset[tuple[str, str]] = frozenset(),
    ) -> str | None:
        """Return the body `send_turn()` wraps in a `SkillReference` `<SystemInterjection>` for
        this turn only, or `None` if `tokens` names no discoverable skill. `tokens` is every
        `/<name>` slug `send_turn()` already found in the turn's prompt via
        `_skill_mention_tokens()` -- each resolved against the typed catalog (bare name via tier
        precedence, or an exact `<namespace>:<name>` fqsn) and skipped if its canonical
        `(namespace, name)` evaluates to `"deny"` or is in `exclude` (the skill a leading-mention
        unconditional activation already fully handled -- see
        `_build_user_skill_activation_interjection`). Always lists a mentioned skill by its
        canonical name, never the alias the user may have typed. Reminds the model to load it via
        `ActivateSkill`. See docs/specs/skills.md."""
        if not tokens:
            return None
        self._ensure_skill_catalog()
        catalog = self._skill_catalog_registry.typed()
        mentioned: list[Skill] = []
        seen: set[tuple[str, str]] = set()
        for token in tokens:
            skill = catalog.resolve_reference(token)
            if skill is None:
                continue
            skill_id = (skill.namespace, skill.name)
            if skill_id in exclude or skill_id in seen:
                continue
            if evaluate_skill(self.config.skill_rules, skill_id) == "deny":
                continue
            seen.add(skill_id)
            mentioned.append(skill)
        if not mentioned:
            return None
        return (
            "Your message mentions one or more skills by name. Load a skill's full instructions "
            "with ActivateSkill(namespace=\"<namespace>\", name=\"<name>\") before acting on it.\n"
            + self._format_skill_list(mentioned)
        )

    def _build_user_skill_activation_interjection(self, skill: Skill) -> UserSkillActivation | None:
        """When `skill` (the prompt's leading `/<token>` slug, already resolved by the caller
        against the typed catalog -- see `_leading_skill_token()`) is non-`"deny"`-verdicted and
        clears the `onActivateSkill` hook, return a `UserSkillActivation` whose `body` carries the
        exact same `{namespace, name, content, files, tokens}` JSON payload `ActivateSkill` would
        return (built by the same `skill_activation_payload()` both paths share), so the model can
        apply the skill immediately with no `ActivateSkill` round trip.

        An `"ask"`-verdicted skill is auto-promoted to `"allow"` for the rest of this session
        (`apply_skill_permission_grant(scope="session")`, no interactive prompt raised) before its
        content is injected: typing `/<name>` as the leading token of a message *is* the user's
        approval, the same way answering an interactive ask with "Allow" would be -- there's no
        separate confirmation to ask for. This only ever widens `"ask"` to `"allow"`; it never
        touches a `"deny"` verdict.

        Returns `None` when `skill`'s verdict is `"deny"`, or an `onActivateSkill` handler vetoes
        the activation -- gets no special treatment at all (as if the user's message didn't start
        with a skill reference), the same whether the catalog was built with that verdict already
        in place or the skill was denied later in this session and the (unrebuilt) catalog still
        holds it; the latter case logs a `logger.warning()` so the silent skip is still observable.
        See docs/specs/skills.md.
        """
        skill_id = (skill.namespace, skill.name)
        verdict = evaluate_skill(self.config.skill_rules, skill_id)
        if verdict == "deny":
            logger.warning(
                "Leading skill mention /%s resolved to %s but its skillRules verdict is deny; "
                "skipping activation.", skill.name, skill_id)
            return None
        if verdict == "ask":
            # Deferred import: `klorb.permissions.skill_grant` pulls in `klorb.permissions.grant`,
            # which imports `klorb.process_config` -- itself upstream of `klorb.session` (and so
            # this module) in the import graph. A module-level import here would cycle back
            # through the partially-initialized `klorb.process_config`, the same reason
            # `klorb.permissions.resource.SkillResource.apply_grant` defers this same import.
            from klorb.permissions.skill_grant import apply_skill_permission_grant
            logger.debug(
                "Leading skill mention /%s auto-promoting %s from ask to allow for this session.",
                skill.name, skill_id)
            apply_skill_permission_grant(
                action="allow", scope="session", session_config=self.config,
                process_config=None, skill_id=skill_id)
        if self.fire_activate_skill_hook(
                skill_namespace=skill.namespace, skill_name=skill.name) is not None:
            # Denied by an `onActivateSkill` handler: no special treatment, as if the leading
            # mention hadn't resolved to a skill at all -- the model still has to call
            # `ActivateSkill` and go through the normal flow, where the hook gets another say.
            return None
        self.grant_skill_bash_commands(skill)
        payload = skill_activation_payload(skill)
        body = (
            f"The user has invoked skill {skill.name}. Read the skill JSON that follows plus "
            "the user's prompt, then apply this skill:\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        return UserSkillActivation(body=body, skill_id=skill_id)

    def fire_activate_skill_hook(self, *, skill_namespace: str, skill_name: str) -> str | None:
        """Dispatch `onActivateSkill` for `(skill_namespace, skill_name)`, about to be activated
        -- called by `_build_user_skill_activation_interjection` (the leading-mention fast path)
        and by `ActivateSkillTool.apply()` (the ordinary model-driven call), after each has
        already resolved and gated the skill through `skillRules`.

        `HookInput.is_user_mentioned`/`is_user_activated` are read off this turn's own
        `_current_turn_mentioned_skill_ids`/`_current_turn_leading_skill_id` (set by
        `send_turn()`), not passed in by the caller -- so both call sites report the same facts
        about what the user actually typed, regardless of which one is asking.

        Returns a denial message when the aggregate `HookOutput` vetoes the activation
        (`success=False`, or a `permission` of `"ask"`/`"deny"`), `None` otherwise.
        """
        skill_id = (skill_namespace, skill_name)
        result = self._dispatch_hook(
            "onActivateSkill", skill_name=skill_name, skill_namespace=skill_namespace,
            is_user_mentioned=skill_id in self._current_turn_mentioned_skill_ids,
            is_user_activated=skill_id == self._current_turn_leading_skill_id)
        if result.success is False or result.permission in ("deny", "ask"):
            return result.message or (
                f"Skill activation {skill_namespace}/{skill_name} blocked by onActivateSkill "
                "hook policy.")
        return None

    def grant_skill_bash_commands(self, skill: Skill) -> None:
        """Pre-authorize `skill`'s `metadata.klorb.bashCommands` argv patterns (see
        `klorb.tools.skill.common.skill_bash_command_patterns`) as session-`allow` `commandRules`
        -- called by both activation paths once the `onActivateSkill` hook has cleared, so a
        skill's own frontmatter can ship the bash commands its instructions need without a
        workspace's `commandRules.allow` having to list them separately. Idempotent: granting the
        same pattern again on a later activation is a no-op (`RuleGrantWriter.apply_decision`
        dedupes against the existing `allow` list).
        """
        patterns = skill_bash_command_patterns(skill.raw)
        if not patterns:
            return
        # Deferred import: `klorb.permissions.command_grant` pulls in `klorb.session`
        # (`SessionConfig`), which would cycle back through this still-initializing module -- the
        # same reason `apply_skill_permission_grant` is deferred above.
        from klorb.permissions.command_grant import apply_command_permission_grant
        for pattern in patterns:
            apply_command_permission_grant(
                action="allow", scope="session", session_config=self.config,
                process_config=None, argv=pattern, patterns=[pattern])
        logger.debug(
            "Skill %s/%s granted %d session bashCommands pattern(s).",
            skill.namespace, skill.name, len(patterns))
