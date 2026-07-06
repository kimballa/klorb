# © Copyright 2026 Aaron Kimball
"""Tests for klorb.system_prompts."""

from pathlib import Path

import pytest

from klorb.system_prompts import (
    DEFAULT_SYS_FILENAME,
    SYSTEM_PROMPTS_SUBDIR,
    mangle_model_name,
    resolve_prompt_file,
)


@pytest.fixture
def user_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the user override tier at an empty temp dir, so a developer's real
    `~/.config/klorb/system_prompts.d/` files can't leak into these tests."""
    monkeypatch.setattr("klorb.system_prompts.KLORB_CONFIG_DIR", tmp_path)
    return tmp_path


def test_mangle_model_name_replaces_slashes_and_colons() -> None:
    assert mangle_model_name("poolside/laguna-m.1:free") == "poolside__laguna-m.1__free"


def test_mangle_model_name_passes_plain_names_through() -> None:
    assert mangle_model_name("alpha") == "alpha"


def test_resolve_prompt_file_reads_packaged_default(user_config_dir: Path) -> None:
    content = resolve_prompt_file(DEFAULT_SYS_FILENAME)

    assert content is not None
    assert "klorb" in content


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
