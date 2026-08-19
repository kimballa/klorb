# © Copyright 2026 Aaron Kimball
"""System-prompt resolution: the shared file-lookup primitive and the per-session assembler.
"""

from __future__ import annotations

import importlib.resources
from typing import TYPE_CHECKING, Any

import yaml

from klorb.paths import get_klorb_config_dir

if TYPE_CHECKING:
    # isort: off
    # These are used only in annotations. Importing them for real would be circular:
    # `klorb.role` and `klorb.models.model` both import the file-lookup primitives from this
    # module, and `klorb.session` imports `SystemPrompt` from it. `SystemPrompt` holds a
    # reference to the live `SessionConfig` object (never copies it), so a mid-session
    # `config.model` change is reflected on the next `resolve()` call.
    from klorb.models.model import Model
    from klorb.models.registry import ModelRegistry
    from klorb.process_config import ProcessConfig
    from klorb.role import Role
    from klorb.session import SessionConfig
    # isort: on


SYSTEM_PROMPTS_SUBDIR = "system_prompts.d"
"""Name of the `system_prompts.d/` tree, rooted both at `$KLORB_CONFIG_DIR` and inside
the `klorb.resources` package."""

ROLES_SUBDIR = "roles"
"""Name of the `roles/` subtree within `system_prompts.d/`, holding one directory per
operating role."""

DEFAULT_SYS_FILENAME = "default_sys.md"
"""Filename of the role- and model-agnostic default system prompt at the top of a
`system_prompts.d/` tree."""

DEFAULT_SYSTEM_PROMPT = "You are klorb, a helpful coding and software engineering assistant."
"""Last-resort system prompt used only if `default_sys.md` is missing from both the user
override tree and the packaged `klorb.resources` tree."""


def mangle_model_name(model_name: str) -> str:
    """Turn a model identifier into a filesystem-safe,
    collision-free filename stem by replacing `/` and `:` with `__`.

    Model identifiers are already vendor-qualified, so this mangling alone is enough to
    keep filenames unique without needing a separate provider-name directory tier.
    """
    return model_name.replace("/", "__").replace(":", "__")


def resolve_prompt_file(relative_path: str) -> str | None:
    """Return the contents of `relative_path` within `system_prompts.d/`, or `None` if it
    exists in neither tier.

    Checks, in order: the user override at
    `$KLORB_CONFIG_DIR/system_prompts.d/<relative_path>`, then the built-in default packaged
    at `klorb.resources/system_prompts.d/<relative_path>`.
    """
    user_path = get_klorb_config_dir() / SYSTEM_PROMPTS_SUBDIR / relative_path
    if user_path.is_file():
        return user_path.read_text()

    packaged_path = (
        importlib.resources.files("klorb.resources")
        .joinpath(SYSTEM_PROMPTS_SUBDIR)
        .joinpath(relative_path)
    )
    if packaged_path.is_file():
        return packaged_path.read_text()

    return None


def wrap_agent_role(role_prompt: str) -> str:
    """Wrap `role_prompt` in an `<AgentRole>...</AgentRole>` tag pair, so it reads as a
    role-specific addendum layered onto the role-and-model-agnostic default prompt ahead of
    it, rather than a competing, free-standing prompt."""
    return f"<AgentRole>\n{role_prompt}\n</AgentRole>"


class SystemPrompt:
    """Resolves and assembles the system prompt for a session, decoupling that logic from
    `Session` so the same resolution is available to both a live turn and the `klorb
    system-prompt` CLI subcommand without constructing a full `Session`.

    Holds references (never copies) to the live `SessionConfig`, `Role`, and
    `ModelRegistry`, so `resolve()` always reflects the *current* `config.model`.
    """

    def __init__(
        self,
        config: "SessionConfig",
        role: Role,
        model_registry: ModelRegistry,
        process_config: "ProcessConfig | None" = None,
    ) -> None:
        self._config = config
        self._role = role
        self._model_registry = model_registry
        self._process_config = process_config
        """The `ProcessConfig` this prompt was constructed with, or `None`. Read only for the
        `config` block of `_metadata_section()`."""

    def _active_model(self) -> Model | None:
        """Return the registered `Model` for `config.model`, or `None` if it isn't
        registered."""
        try:
            return self._model_registry.get(self._config.model)
        except KeyError:
            return None

    def default_prompt(self) -> str:
        """Return the role-agnostic "default walk" result: the active model's own prompt
        file, then `default_sys.md`, then the hardcoded `DEFAULT_SYSTEM_PROMPT` safety net."""
        model = self._active_model()
        default_prompt = model.system_prompt() if model is not None else None
        if default_prompt is None:
            default_prompt = self._default_sys_prompt()
        return default_prompt

    def _default_sys_prompt(self) -> str:
        """Return the `default_sys.md` prompt file (user override tier, then packaged tier),
        falling back to the hardcoded `DEFAULT_SYSTEM_PROMPT` constant only if that file
        exists in neither tier."""
        prompt = resolve_prompt_file(DEFAULT_SYS_FILENAME)
        return prompt if prompt is not None else DEFAULT_SYSTEM_PROMPT

    def role_prompt(self) -> str | None:
        """Return the role-specific "role walk" result, or `None` if this role has no prompt
        file in either tier. Tries the active model's role-specific variant first, then
        the role's model-agnostic default."""
        model = self._active_model()
        return self._role.system_prompt(model)

    def _metadata_section(self) -> str:
        """Build a `## Metadata` section reporting the current model name plus a nested `config`
        block of the `compatibility.claude*` flags, rendered as YAML. Appended at the end of
        the assembled system prompt so the model always knows its own identifier and the
        compatibility flags in effect."""
        metadata: dict[str, Any] = {"model": self._config.model}
        model = self._active_model()
        knowledge_cutoff = model.knowledge_cutoff() if model is not None else None
        release_date = model.release_date() if model is not None else None
        if knowledge_cutoff is not None:
            metadata["knowledgeCutoff"] = knowledge_cutoff
        elif release_date is not None:
            # Model release date is a reasonable proxy for knowledge cutoff.
            metadata["knowledgeCutoff"] = release_date
        metadata["config"] = {
            "compatibility.claudeMarkdown": (
                self._process_config.compatibility_claude_markdown
                if self._process_config is not None else False
            ),
            "compatibility.claudeSkills": (
                self._process_config.compatibility_claude_skills
                if self._process_config is not None else False
            ),
        }
        yaml_str = yaml.safe_dump(metadata, sort_keys=False)
        return f"## Metadata\n\n```yaml\n{yaml_str}```"

    def resolve(self) -> str:
        """Resolve the full system prompt by concatenating the default walk's result with
        the role walk's, then appending a `Metadata` section. The default walk's result is
        always the base; when the role walk also produces a prompt, it's wrapped in an
        `<AgentRole>` tag and appended after the default prompt,
        separated by a blank line. When the role walk resolves nothing, the default walk's
        result is used as-is. The `Metadata` section is always last, regardless of role.

        Re-derived fresh on each call, so it always reflects the *current* `config.model`."""
        default_prompt = self.default_prompt()
        role_prompt = self.role_prompt()
        if role_prompt is None:
            body = default_prompt
        else:
            body = f"{default_prompt}\n\n{wrap_agent_role(role_prompt)}"
        return f"{body}\n\n{self._metadata_section()}"
