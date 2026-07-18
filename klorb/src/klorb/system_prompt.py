# © Copyright 2026 Aaron Kimball
"""System-prompt resolution: the shared file-lookup primitive and the per-session assembler.

Two layers live here:

* The low-level file-lookup primitive. `Role.system_prompt()`, `Model.system_prompt()`, and
  `SystemPrompt._default_sys_prompt()` each resolve one relative path within a
  `system_prompts.d/` tree via `resolve_prompt_file()`: a user-writable override under
  `$KLORB_CONFIG_DIR/system_prompts.d/`, falling back to the built-in default shipped inside
  the `klorb.resources` package.

* The high-level `SystemPrompt`, which runs the two independent resolver walks (a
  role-agnostic "default walk" and a role-specific "role walk") that `Session` previously ran
  inline, exposing the assembled prompt both for live use by a turn and for introspection by
  the `klorb system-prompt` CLI subcommand (see `klorb.cli.run_system_prompt_cli`).

See docs/specs/roles-and-system-prompts.md for the full resolution order across those call
sites and the prompt-file tree layout this builds on.
"""

from __future__ import annotations

import importlib.resources
from typing import TYPE_CHECKING

from klorb.paths import KLORB_CONFIG_DIR

if TYPE_CHECKING:
    # isort: off
    # These are used only in annotations. Importing them for real would be circular:
    # `klorb.role` and `klorb.models.model` both import the file-lookup primitives from this
    # module, and `klorb.session` imports `SystemPrompt` from it. `SystemPrompt` holds a
    # reference to the live `SessionConfig` object (never copies it), so a mid-session
    # `config.model` change is reflected on the next `resolve()` call.
    from klorb.models.model import Model
    from klorb.models.registry import ModelRegistry
    from klorb.role import Role
    from klorb.session import SessionConfig
    # isort: on


SYSTEM_PROMPTS_SUBDIR = "system_prompts.d"
"""Name of the `system_prompts.d/` tree, rooted both at `$KLORB_CONFIG_DIR` (user overrides)
and inside the `klorb.resources` package (built-in defaults) — see `resolve_prompt_file()`."""

ROLES_SUBDIR = "roles"
"""Name of the `roles/` subtree within `system_prompts.d/`, holding one directory per
operating role (`roles/<role>/default.md`, `roles/<role>/<mangled model name>.md`) — see
`klorb.role.Role.system_prompt()`."""

DEFAULT_SYS_FILENAME = "default_sys.md"
"""Filename of the role- and model-agnostic default system prompt at the top of a
`system_prompts.d/` tree — see `SystemPrompt.default_prompt()`."""

DEFAULT_SYSTEM_PROMPT = "You are klorb, a helpful coding and software engineering assistant."
"""Last-resort system prompt used only if `default_sys.md` is missing from both the user
override tree and the packaged `klorb.resources` tree — in practice this never triggers,
since the packaged copy always ships with klorb (see `resources/system_prompts.d/default_sys.md`)."""


def mangle_model_name(model_name: str) -> str:
    """Turn a model identifier (e.g. `"poolside/laguna-m.1:free"`) into a filesystem-safe,
    collision-free filename stem by replacing `/` and `:` with `__`.

    Model identifiers are already vendor-qualified (`<vendor>/<model>[:<variant>]`), so this
    mangling alone is enough to keep filenames unique without needing a separate
    provider-name directory tier.
    """
    return model_name.replace("/", "__").replace(":", "__")


def resolve_prompt_file(relative_path: str) -> str | None:
    """Return the contents of `relative_path` within `system_prompts.d/`, or `None` if it
    exists in neither tier.

    Checks, in order: the user override at
    `$KLORB_CONFIG_DIR/system_prompts.d/<relative_path>`, then the built-in default packaged
    at `klorb.resources/system_prompts.d/<relative_path>`.
    """
    user_path = KLORB_CONFIG_DIR / SYSTEM_PROMPTS_SUBDIR / relative_path
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
    it, rather than a competing, free-standing prompt — see `SystemPrompt.resolve()`."""
    return f"<AgentRole>\n{role_prompt}\n</AgentRole>"


class SystemPrompt:
    """Resolves and assembles the system prompt for a session, decoupling that logic from
    `Session` so the same resolution is available to both a live turn and the `klorb
    system-prompt` CLI subcommand without constructing a full `Session`.

    Holds references (never copies) to the live `SessionConfig`, `Role`, and
    `ModelRegistry`, so `resolve()` always reflects the *current* `config.model` — the same
    mid-session model-change behavior `Session._resolve_system_prompt()` had when it ran
    the same walks inline (see docs/specs/roles-and-system-prompts.md).
    """

    def __init__(self, config: "SessionConfig", role: Role, model_registry: ModelRegistry) -> None:
        self._config = config
        self._role = role
        self._model_registry = model_registry

    def _active_model(self) -> Model | None:
        """Return the registered `Model` for `config.model`, or `None` if it isn't
        registered."""
        try:
            return self._model_registry.get(self._config.model)
        except KeyError:
            return None

    def default_prompt(self) -> str:
        """Return the role-agnostic "default walk" result: the active model's own prompt
        file (`Model.system_prompt()`, skipped when `config.model` isn't registered), then
        `default_sys.md` (via `resolve_prompt_file`), then the hardcoded
        `DEFAULT_SYSTEM_PROMPT` safety net. This walk never comes up empty — its final tier
        is the constant, which never triggers in practice since the packaged `default_sys.md`
        always ships inside `klorb.resources`."""
        model = self._active_model()
        default_prompt = model.system_prompt() if model is not None else None
        if default_prompt is None:
            default_prompt = self._default_sys_prompt()
        return default_prompt

    def _default_sys_prompt(self) -> str:
        """Return the `default_sys.md` prompt file (user override tier, then packaged tier
        — see `resolve_prompt_file`), falling back to the hardcoded `DEFAULT_SYSTEM_PROMPT`
        constant only if that file exists in neither tier."""
        prompt = resolve_prompt_file(DEFAULT_SYS_FILENAME)
        return prompt if prompt is not None else DEFAULT_SYSTEM_PROMPT

    def role_prompt(self) -> str | None:
        """Return the role-specific "role walk" result, or `None` if this role has no prompt
        file in either tier. Tries the active model's role-specific variant first
        (`roles/<role>/<mangled-model>.md`, skipped when the model isn't registered), then
        the role's model-agnostic default (`roles/<role>/default.md`) — each via
        `Role.system_prompt()`, which subclasses may override to return a literal string."""
        model = self._active_model()
        return self._role.system_prompt(model)

    def _metadata_section(self) -> str:
        """Build a `## Metadata` section listing the current model name (and, when the
        active model reports one, its `Model.knowledge_cutoff()`), appended at the end of
        the assembled system prompt so the model always knows its own identifier."""
        lines = [f"* **Model**: `{self._config.model}`"]
        model = self._active_model()
        knowledge_cutoff = model.knowledge_cutoff() if model is not None else None
        if knowledge_cutoff is not None:
            lines.append(f"* **Knowledge cutoff**: {knowledge_cutoff}")
        return "## Metadata\n\n" + "\n".join(lines)

    def resolve(self) -> str:
        """Resolve the full system prompt by concatenating the default walk's result with
        the role walk's, then appending a `Metadata` section. The default walk's result is
        always the base; when the role walk also produces a prompt, it's wrapped in an
        `<AgentRole>` tag (`wrap_agent_role`) and appended after the default prompt,
        separated by a blank line — so a role's instructions layer onto the default ones
        rather than replacing them. When the role walk resolves nothing, the default walk's
        result is used as-is. The `Metadata` section is always last, regardless of role.

        Re-derived fresh on each call, so it always reflects the *current* `config.model` —
        see docs/specs/roles-and-system-prompts.md."""
        default_prompt = self.default_prompt()
        role_prompt = self.role_prompt()
        if role_prompt is None:
            body = default_prompt
        else:
            body = f"{default_prompt}\n\n{wrap_agent_role(role_prompt)}"
        return f"{body}\n\n{self._metadata_section()}"
