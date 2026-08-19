# © Copyright 2026 Aaron Kimball
"""Operating roles: the job a session's agent is performing, independent of which model
runs it. See docs/specs/roles-and-system-prompts.md.
"""

from abc import ABC, abstractmethod

from klorb.models.model import Model
from klorb.system_prompt import ROLES_SUBDIR, resolve_prompt_file

OPERATOR_ROLE_NAME = "operator"
"""`SessionConfig.role_name`'s default: the top-level operating role a klorb session runs
as unless a caller says otherwise."""


class Role(ABC):
    """Base class for the operating role a session's agent performs.

    Concrete subclasses give klorb somewhere to hang role-specific behavior beyond the
    prompt files themselves as those features are built; `NamedRole` covers any role that has
    no dedicated subclass yet.
    """

    @abstractmethod
    def name(self) -> str:
        """Return this role's identifier: the `SessionConfig.role_name` string it was built
        from, and the directory name its prompt files live under
        (`system_prompts.d/roles/<name>/`). Expected to be a filesystem-safe slug."""

    def system_prompt(self, model: Model | None) -> str | None:
        """Return the role-specific system prompt for this role, tuned to `model` when a
        tuned variant exists, or `None` if no prompt file for this role exists at all.

        Checks, most specific first, each via
        `klorb.system_prompt.resolve_prompt_file()`: `roles/<name>/<model.mangled_name()>.md`
        then `roles/<name>/default.md`. Subclasses may override to return a literal string
        without filesystem access.
        """
        if model is not None:
            prompt = resolve_prompt_file(f"{ROLES_SUBDIR}/{self.name()}/{model.mangled_name()}.md")
            if prompt is not None:
                return prompt
        return resolve_prompt_file(f"{ROLES_SUBDIR}/{self.name()}/default.md")

    def repertoire(self) -> list[str]:
        """Return the names of the specialist subagents and role-specific tools this role
        may employ. No dispatch mechanism exists yet, so every role's repertoire is empty."""
        return []


class NamedRole(Role):
    """A role identified only by its name string, with no dedicated subclass.

    Covers any `SessionConfig.role_name` that `get_role()` doesn't recognize: it
    triangulates the right behavior purely from the name.
    """

    def __init__(self, role_name: str) -> None:
        self._role_name = role_name

    def name(self) -> str:
        return self._role_name


class OperatorRole(Role):
    """The default top-level operating role: the lead agent that owns a coding task end to
    end, with full latitude to research, decide, plan, write docs/code/tests, run and debug,
    and review work, biased toward an iterative
    research/think/decide/plan/execute/verify/analyze loop and toward decomposing large
    problems into ordered, fine-grained tasks. The behavioral instructions themselves live
    in `system_prompts.d/roles/operator/default.md`, not in code.
    """

    def name(self) -> str:
        return OPERATOR_ROLE_NAME


def get_role(role_name: str) -> Role:
    """Return the `Role` implementation for `role_name`: its dedicated subclass when one
    exists, else a `NamedRole` carrying the name as-is."""
    if role_name == OPERATOR_ROLE_NAME:
        return OperatorRole()
    return NamedRole(role_name)
