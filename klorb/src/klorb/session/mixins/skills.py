# © Copyright 2026 Aaron Kimball
"""`SessionSkillsMixin`: workspace context-file discovery and skill-related interjection builders."""

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
    """Workspace context-file discovery and skill-catalog-backed interjection builders."""

    def _build_context_files_interjection(self) -> str | None:
        """Return the body `send_turn()` wraps in a `<SystemInterjection subject=
        "ProjectGuidance">` tag and prepends onto the very first turn's prompt, or `None` if
        there's nothing to say.

        If `config.workspace.trusted` is `False`, workspace context files are ignored.

        Reads each applicable context filename that exists on disk, relative to
        `config.workspace.path`, and wraps each one's contents in a
        `<ContextFile filename="..." priority="N">` tag.

        Additionally checks for `INSTRUCTIONS.md` in `KLORB_CONFIG_DIR` and includes it
        with highest priority when present, even in an untrusted workspace.
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
        workspace root, most authoritative first."""
        filenames = [f"{KLORB_PROJECT_DIR_NAME}/INSTRUCTIONS.md", "AGENTS.md"]
        if self._compatibility_claude_markdown:
            filenames.append("CLAUDE.md")
        return filenames

    def _ensure_skill_catalog(self) -> None:
        """Build this session's skill catalog if it hasn't built one yet."""
        self._skill_catalog_registry.ensure(
            workspace_root=self.config.workspace.path,
            workspace_trusted=self.config.workspace.trusted,
            claude_skills_compat=self._compatibility_claude_skills,
            skill_rules=self.config.skill_rules,
        )

    def reload_skills(self) -> SkillCatalogs:
        """Rebuild this session's skill catalog from a fresh disk scan against the current
        workspace. Prefers the live `ProcessConfig.compatibility_claude_skills` this
        session was constructed with over its own construction-time snapshot."""
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
        session's catalog and its live `skill_rules`."""
        self._ensure_skill_catalog()
        return self._skill_catalog_registry.canonical().discoverable(self.config.skill_rules)

    @staticmethod
    def _format_skill_list(skills: list[Skill]) -> str:
        """Render `skills` as the newline-joined `- <name> (<namespace>): <description>` bullet
        list, always by canonical name. A skill with an empty description
        contributes just `- <name> (<namespace>)`."""
        lines = []
        for skill in skills:
            description = display_skill_description(skill.description)
            lines.append(f"- {skill.name} ({skill.namespace}): {description}" if description
                         else f"- {skill.name} ({skill.namespace})")
        return "\n".join(lines)

    def _build_available_skills_interjection(self, skills: list[Skill]) -> str | None:
        """Return the body `send_turn()` wraps in an `AvailableSkills` `<SystemInterjection>` and
        prepends onto the first turn's prompt, or `None` if no skill is discoverable."""
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
        this turn only, or `None` if `tokens` names no discoverable skill."""
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
        """When `skill` (the prompt's leading `/<token>` slug) is non-`"deny"`-verdicted and
        clears the `onActivateSkill` hook, return a `UserSkillActivation` whose `body` carries
        the `ActivateSkill`-equivalent JSON payload so the model can apply the skill
        immediately with no round trip.

        An `"ask"`-verdicted skill is auto-promoted to `"allow"` for the rest of this session
        before its content is injected: typing `/<name>` as the leading token of a message
        *is* the user's approval.

        Returns `None` when `skill`'s verdict is `"deny"`, or an `onActivateSkill` handler
        vetoes the activation.
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
        """Dispatch `onActivateSkill` for `(skill_namespace, skill_name)`, about to be activated.

        `HookInput.is_user_mentioned`/`is_user_activated` are read off this turn's own
        `_current_turn_mentioned_skill_ids`/`_current_turn_leading_skill_id`.

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
        """Pre-authorize `skill`'s `metadata.klorb.bashCommands` argv patterns as session-`allow`
        `commandRules`, so a skill's frontmatter can ship the bash commands its instructions need
        without a workspace's `commandRules.allow` listing them separately. Idempotent."""
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
