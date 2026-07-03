# © Copyright 2026 Aaron Kimball
"""Tests for klorb.role."""

from pathlib import Path

import pytest
from fixtures.sample_models.alpha_model import AlphaModel

from klorb.role import COORDINATOR_ROLE_NAME
from klorb.role import CoordinatorRole
from klorb.role import NamedRole
from klorb.role import get_role
from klorb.system_prompts import SYSTEM_PROMPTS_SUBDIR


@pytest.fixture
def user_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the user override tier at an empty temp dir, so a developer's real
    `~/.config/klorb/system_prompts.d/` files can't leak into these tests."""
    monkeypatch.setattr("klorb.system_prompts.KLORB_CONFIG_DIR", tmp_path)
    return tmp_path


def test_get_role_returns_coordinator_for_coordinator_name() -> None:
    role = get_role(COORDINATOR_ROLE_NAME)

    assert isinstance(role, CoordinatorRole)
    assert role.name() == "coordinator"


def test_get_role_returns_named_role_for_unrecognized_name() -> None:
    role = get_role("auditor")

    assert isinstance(role, NamedRole)
    assert role.name() == "auditor"


def test_coordinator_system_prompt_resolves_packaged_default(user_config_dir: Path) -> None:
    prompt = CoordinatorRole().system_prompt(None)

    assert prompt is not None
    assert "Coordinator" in prompt


def test_role_system_prompt_returns_none_when_no_files_exist(user_config_dir: Path) -> None:
    assert NamedRole("no-such-role").system_prompt(AlphaModel()) is None


def test_role_model_specific_prompt_beats_role_default(user_config_dir: Path) -> None:
    role_dir = user_config_dir / SYSTEM_PROMPTS_SUBDIR / "roles" / "explorer"
    role_dir.mkdir(parents=True)
    (role_dir / "default.md").write_text("explorer default")
    (role_dir / f"{AlphaModel().mangled_name()}.md").write_text("explorer on alpha")

    role = NamedRole("explorer")

    assert role.system_prompt(AlphaModel()) == "explorer on alpha"
    assert role.system_prompt(None) == "explorer default"


def test_role_falls_back_to_role_default_when_no_model_specific_file(user_config_dir: Path) -> None:
    role_dir = user_config_dir / SYSTEM_PROMPTS_SUBDIR / "roles" / "explorer"
    role_dir.mkdir(parents=True)
    (role_dir / "default.md").write_text("explorer default")

    assert NamedRole("explorer").system_prompt(AlphaModel()) == "explorer default"


def test_repertoire_is_empty_for_every_role() -> None:
    assert CoordinatorRole().repertoire() == []
    assert NamedRole("auditor").repertoire() == []
