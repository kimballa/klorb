# © Copyright 2026 Aaron Kimball
"""Operating roles: the job a session's agent is performing, independent of which model
runs it.

A `Role` names what the agent is *for* this session — coordinating a coding task end to
end, exploring a codebase, auditing a change — and resolves the role-specific tiers of the
session's system prompt. `Session` constructs its `Role` itself, from
`SessionConfig.role_name`, via `get_role()`; callers never hand a `Role` object in, so a
session's `Role` can never disagree with its `config.role_name`. See
docs/specs/roles-and-system-prompts.md.
"""

from abc import ABC
from abc import abstractmethod

from klorb.models.model import Model
from klorb.system_prompts import ROLES_SUBDIR
from klorb.system_prompts import resolve_prompt_file

COORDINATOR_ROLE_NAME = "coordinator"
"""`SessionConfig.role_name`'s default: the top-level operating role a klorb session runs
as unless a caller (e.g. a future subagent-spawning code path) says otherwise."""


class Role(ABC):
    """Base class for the operating role a session's agent performs.

    Concrete subclasses give klorb somewhere to hang role-specific behavior beyond the
    prompt files themselves (specialist tool access, a curated subagent repertoire) as those
    features are built; `NamedRole` covers any role that has no dedicated subclass yet.
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
        `klorb.system_prompts.resolve_prompt_file()` (user override tier, then packaged
        tier): `roles/<name>/<model.mangled_name()>.md` (skipped when `model` is `None`,
        e.g. the active model string has no registered `Model`), then
        `roles/<name>/default.md`. Subclasses (e.g. test fixtures) may override to return a
        literal string without filesystem access.
        """
        if model is not None:
            prompt = resolve_prompt_file(f"{ROLES_SUBDIR}/{self.name()}/{model.mangled_name()}.md")
            if prompt is not None:
                return prompt
        return resolve_prompt_file(f"{ROLES_SUBDIR}/{self.name()}/default.md")

    def repertoire(self) -> list[str]:
        """Return the names of the specialist subagents and role-specific tools this role
        may employ. A placeholder for the multi-agent teamwork described in
        docs/specs/roles-and-system-prompts.md's "Out of scope": no dispatch mechanism
        exists yet, so every role's repertoire is empty."""
        return []


class NamedRole(Role):
    """A role identified only by its name string, with no dedicated subclass.

    Covers any `SessionConfig.role_name` that `get_role()` doesn't recognize: it
    triangulates the right behavior purely from the name — its system prompt comes from
    whatever `system_prompts.d/roles/<name>/` files exist (falling through to the
    model-specific and default tiers otherwise — see `Session._resolve_system_prompt`).
    """

    def __init__(self, role_name: str) -> None:
        self._role_name = role_name

    def name(self) -> str:
        return self._role_name


class CoordinatorRole(Role):
    """The default top-level operating role: the lead agent that owns a coding task end to
    end, with full latitude to research, decide, plan, write docs/code/tests, run and debug,
    and review work (its own or another agent's), biased toward an iterative
    research/think/decide/plan/execute/verify/analyze loop and toward decomposing large
    problems into ordered, fine-grained tasks. The behavioral instructions themselves live
    in `system_prompts.d/roles/coordinator/default.md`, not in code.
    """

    def name(self) -> str:
        return COORDINATOR_ROLE_NAME


def get_role(role_name: str) -> Role:
    """Return the `Role` implementation for `role_name`: its dedicated subclass when one
    exists (today only `CoordinatorRole`), else a `NamedRole` carrying the name as-is."""
    if role_name == COORDINATOR_ROLE_NAME:
        return CoordinatorRole()
    return NamedRole(role_name)
