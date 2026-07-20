# © Copyright 2026 Aaron Kimball
"""`SessionSkillsMixin`: workspace context-file discovery and every skill-related
`<SystemInterjection>` body `send_turn()` may prepend onto a turn's prompt -- the standing
`AvailableSkills` catalog, the per-turn `SkillReference` reminder, and the leading-mention
`UserSkillActivation` shortcut. See docs/specs/skills.md."""

import json

from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME
from klorb.permissions.skill_access import evaluate_skill
from klorb.session.events import UserSkillActivation
from klorb.session.mixins._base import SessionBase
from klorb.tools.skill.catalog import get_skill_catalog_registry
from klorb.tools.skill.common import skill_activation_payload
from klorb.tools.skill.model import Skill


class SessionSkillsMixin(SessionBase):
    """Workspace context-file (`AGENTS.md`/`CLAUDE.md`/`.klorb/INSTRUCTIONS.md`) discovery and
    the skill-catalog-backed interjection builders `send_turn()` calls into."""

    def _build_context_files_interjection(self) -> str | None:
        """Return the body `send_turn()` wraps in a `<SystemInterjection subject=
        "ProjectGuidance">` tag and prepends onto the very first turn's prompt, or `None` if
        there's nothing to say.

        Returns `None` outright, without touching the filesystem at all, whenever
        `config.workspace.trusted` is `False`: `.klorb/INSTRUCTIONS.md`, `AGENTS.md`, and
        `CLAUDE.md` are project-supplied content, and a hostile, downloaded-and-unzipped
        repository could ship any of them to smuggle instructions into the model's context the
        moment the user runs klorb from inside it — the same risk `.klorb/klorb-config.json`'s
        own trust gate exists to close (see docs/specs/projects-and-trust.md). So none of them
        are ever read into the prompt until the user has explicitly trusted the workspace,
        exactly like that config layer.

        Otherwise reads each of `_applicable_context_filenames()` (in priority order) that
        exists on disk, relative to `config.workspace.path`, and wraps each one's contents in
        a `<ContextFile filename="..." priority="N">` tag, `N` starting at `1` in that same
        priority order — giving the model an explicit signal for which file should win if two
        ever conflict."""
        if not self.config.workspace.trusted:
            return None

        context_files: list[tuple[str, str]] = []
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
        workspace root), then `AGENTS.md` (priority 2 — klorb's own root-level convention),
        then `CLAUDE.md` (priority 3) when `_compatibility_claude_markdown` is enabled."""
        filenames = [f"{KLORB_PROJECT_DIR_NAME}/INSTRUCTIONS.md", "AGENTS.md"]
        if self._compatibility_claude_markdown:
            filenames.append("CLAUDE.md")
        return filenames

    def _ensure_skill_catalog(self) -> None:
        """Build the process-wide skill catalog if this process hasn't built one yet -- a no-op
        after the first call. See `klorb.tools.skill.catalog.SkillCatalogRegistry.ensure`."""
        get_skill_catalog_registry().ensure(
            workspace_root=self.config.workspace.path,
            workspace_trusted=self.config.workspace.trusted,
            claude_skills_compat=self._compatibility_claude_skills,
        )

    def _discover_skills(self) -> list[Skill]:
        """Every currently non-`deny`-verdicted skill, precedence-deduped by name, from the
        process-wide catalog and this session's live `skill_rules`. No disk access -- see
        `klorb.tools.skill.catalog.SkillCatalog.discoverable` and docs/specs/skills.md."""
        self._ensure_skill_catalog()
        return get_skill_catalog_registry().canonical().discoverable(self.config.skill_rules)

    @staticmethod
    def _format_skill_list(skills: list[Skill]) -> str:
        """Render `skills` as the newline-joined `- <name> (<namespace>): <description>` bullet
        list shared by both skill interjections, always by canonical name (a skill's directory
        basename), never a frontmatter-name alias. A skill with an empty description contributes
        just `- <name> (<namespace>)`."""
        lines = [
            f"- {skill.name} ({skill.namespace}): {skill.description}" if skill.description
            else f"- {skill.name} ({skill.namespace})"
            for skill in skills
        ]
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
            "instructions with ActivateSkill(namespace=\"<namespace>\", name=\"<name>\") and "
            "follow them. Use SearchSkills to narrow this list by keyword.\n"
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
        catalog = get_skill_catalog_registry().typed()
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

    def _build_user_skill_activation_interjection(self, token: str) -> UserSkillActivation | None:
        """When `token` (the prompt's leading `/<token>` slug, from `_leading_skill_token()`)
        resolves to a discoverable, `"allow"`-verdicted skill, return a `UserSkillActivation`
        whose `body` carries the exact same `{namespace, name, content, files, tokens}` JSON
        payload `ActivateSkill` would return (built by the same `skill_activation_payload()` both
        paths share), so the model can apply the skill immediately with no `ActivateSkill` round
        trip.

        Returns `None` when `token` doesn't resolve to a skill, or when it resolves but isn't
        `"allow"`-verdicted -- a `"deny"` skill gets no special treatment at all (as if the user's
        message didn't start with a skill reference), and an `"ask"`-verdicted skill falls back to
        the ordinary `SkillReference` reminder instead, so the model still has to call
        `ActivateSkill` and go through the normal approval flow -- a prompt-leading `/name` never
        bypasses `skillRules` approval. See docs/specs/skills.md.
        """
        self._ensure_skill_catalog()
        skill = get_skill_catalog_registry().typed().resolve_reference(token)
        if skill is None:
            return None
        skill_id = (skill.namespace, skill.name)
        if evaluate_skill(self.config.skill_rules, skill_id) != "allow":
            return None
        payload = skill_activation_payload(skill)
        body = (
            f"The user has invoked skill {skill.name}. Read the skill JSON that follows plus "
            "the user's prompt, then apply this skill:\n"
            + json.dumps(payload)
        )
        return UserSkillActivation(body=body, skill_id=skill_id)
