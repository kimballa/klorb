# © Copyright 2026 Aaron Kimball
"""Tests for klorb.system_prompt."""

from pathlib import Path

import pytest
from fixtures.sample_models import sample_model_registry

from klorb.role import COORDINATOR_ROLE_NAME, get_role
from klorb.system_prompt import (
    DEFAULT_SYS_FILENAME,
    SYSTEM_PROMPTS_SUBDIR,
    SystemPrompt,
    mangle_model_name,
    resolve_prompt_file,
    wrap_agent_role,
)


@pytest.fixture
def user_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the user override tier at an empty temp dir, so a developer's real
    `~/.config/klorb/system_prompts.d/` files can't leak into these tests."""
    monkeypatch.setattr("klorb.system_prompt.KLORB_CONFIG_DIR", tmp_path)
    return tmp_path


# What the default walk resolves for the packaged default_sys.md, computed via the same
# resolve_prompt_file() SystemPrompt uses, so this stays correct even if a developer's own
# user-tier override files exist.
DEFAULT_PROMPT = resolve_prompt_file(DEFAULT_SYS_FILENAME)

# What a coordinator-role session's role walk resolves to.
COORDINATOR_PROMPT = resolve_prompt_file("roles/coordinator/default.md")

# A composed coordinator prompt: default + <AgentRole>-wrapped role addendum.
COMPOSED_COORDINATOR_PROMPT = (
    f"{DEFAULT_PROMPT}\n\n{wrap_agent_role(COORDINATOR_PROMPT)}")  # type: ignore[arg-type]


def _with_metadata(prompt: str, model: str, knowledge_cutoff: str | None = None) -> str:
    """Append the expected ``## Metadata`` section that `SystemPrompt.resolve()` adds."""
    lines = [f"* **Model**: `{model}`"]
    if knowledge_cutoff is not None:
        lines.append(f"* **Knowledge cutoff**: {knowledge_cutoff}")
    return f"{prompt}\n\n## Metadata\n\n" + "\n".join(lines)


def _system_prompt(model: str, role_name: str = COORDINATOR_ROLE_NAME) -> SystemPrompt:
    """Build a `SystemPrompt` for `model`/`role_name` against the sample-models registry,
    without needing a full `Session`."""
    from klorb.session import SessionConfig
    config = SessionConfig(model=model, role_name=role_name)
    role = get_role(role_name)
    registry = sample_model_registry()
    return SystemPrompt(config, role, registry)


def test_wrap_agent_role_wraps_in_tag(user_config_dir: Path) -> None:
    assert wrap_agent_role("do stuff") == "<AgentRole>\ndo stuff\n</AgentRole>"


def test_default_prompt_resolves_default_sys_md(user_config_dir: Path) -> None:
    sp = _system_prompt("some/unregistered-model")
    assert sp.default_prompt() == DEFAULT_PROMPT


def test_default_prompt_uses_registered_model_prompt(user_config_dir: Path) -> None:
    sp = _system_prompt("alpha")
    assert sp.default_prompt() == "You are Alpha."


def test_role_prompt_resolves_coordinator_default(user_config_dir: Path) -> None:
    sp = _system_prompt("some/unregistered-model")
    assert sp.role_prompt() == COORDINATOR_PROMPT


def test_role_prompt_returns_none_for_unknown_role(user_config_dir: Path) -> None:
    sp = _system_prompt("some/unregistered-model", role_name="no-such-role")
    assert sp.role_prompt() is None


def test_role_prompt_uses_model_specific_role_file(user_config_dir: Path) -> None:
    role_dir = user_config_dir / SYSTEM_PROMPTS_SUBDIR / "roles" / "explorer"
    role_dir.mkdir(parents=True)
    (role_dir / "default.md").write_text("explorer default")
    (role_dir / "alpha.md").write_text("explorer on alpha")

    sp = _system_prompt("alpha", role_name="explorer")
    assert sp.role_prompt() == "explorer on alpha"


def test_resolve_combines_default_and_role_for_coordinator(user_config_dir: Path) -> None:
    sp = _system_prompt("some/unregistered-model")
    assert sp.resolve() == _with_metadata(COMPOSED_COORDINATOR_PROMPT, "some/unregistered-model")


def test_resolve_returns_default_when_role_walk_is_none(user_config_dir: Path) -> None:
    sp = _system_prompt("some/unregistered-model", role_name="no-such-role")
    assert sp.resolve() == _with_metadata(DEFAULT_PROMPT or "", "some/unregistered-model")


def test_resolve_uses_registered_model_prompt_as_default(user_config_dir: Path) -> None:
    sp = _system_prompt("alpha")
    expected = f"You are Alpha.\n\n{wrap_agent_role(COORDINATOR_PROMPT)}"  # type: ignore[arg-type]
    assert sp.resolve() == _with_metadata(expected, "alpha", knowledge_cutoff="2024-01-01")


def test_resolve_reflects_mid_session_model_change(user_config_dir: Path) -> None:
    sp = _system_prompt("some/unregistered-model")
    assert sp.resolve() == _with_metadata(COMPOSED_COORDINATOR_PROMPT, "some/unregistered-model")

    # Simulate a mid-session model change.
    sp._config.model = "alpha"
    expected = f"You are Alpha.\n\n{wrap_agent_role(COORDINATOR_PROMPT)}"  # type: ignore[arg-type]
    assert sp.resolve() == _with_metadata(expected, "alpha", knowledge_cutoff="2024-01-01")


def test_mangle_model_name_replaces_slashes_and_colons() -> None:
    assert mangle_model_name("poolside/laguna-m.1:free") == "poolside__laguna-m.1__free"


def test_mangle_model_name_passes_plain_names_through() -> None:
    assert mangle_model_name("alpha") == "alpha"


def test_resolve_prompt_file_reads_packaged_default(user_config_dir: Path) -> None:
    content = resolve_prompt_file(DEFAULT_SYS_FILENAME)

    assert content is not None
    assert "Klorb" in content


def test_default_sys_prompt_documents_memories(user_config_dir: Path) -> None:
    content = resolve_prompt_file(DEFAULT_SYS_FILENAME)

    assert content is not None
    assert "## Memories" in content
    assert "ListMemories" in content


def test_default_sys_prompt_documents_ask_user_questions(user_config_dir: Path) -> None:
    content = resolve_prompt_file(DEFAULT_SYS_FILENAME)

    assert content is not None
    assert "## Deciding vs. asking" in content
    assert "AskUserQuestions" in content


def test_default_sys_prompt_documents_bash_tool(user_config_dir: Path) -> None:
    content = resolve_prompt_file(DEFAULT_SYS_FILENAME)

    assert content is not None
    assert "## Bash" in content
    assert "stdout" in content
    assert "exit" in content


def test_resolve_prompt_file_reads_packaged_coordinator_role_prompt(user_config_dir: Path) -> None:
    content = resolve_prompt_file("roles/coordinator/default.md")

    assert content is not None
    assert "Coordinator" in content


def test_resolve_prompt_file_user_override_beats_packaged(user_config_dir: Path) -> None:
    override = user_config_dir / SYSTEM_PROMPTS_SUBDIR / DEFAULT_SYS_FILENAME
    override.parent.mkdir(parents=True)
    override.write_text("user-tier prompt")

    assert resolve_prompt_file(DEFAULT_SYS_FILENAME) == "user-tier prompt"


def test_resolve_prompt_file_user_only_file_is_found(user_config_dir: Path) -> None:
    override = user_config_dir / SYSTEM_PROMPTS_SUBDIR / "roles" / "auditor" / "default.md"
    override.parent.mkdir(parents=True)
    override.write_text("audit everything")

    assert resolve_prompt_file("roles/auditor/default.md") == "audit everything"


def test_resolve_prompt_file_missing_in_both_tiers_returns_none(user_config_dir: Path) -> None:
    assert resolve_prompt_file("roles/no-such-role/default.md") is None
