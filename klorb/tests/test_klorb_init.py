# © Copyright 2026 Aaron Kimball
"""Tests for klorb.klorb_init."""

import json
import os
from pathlib import Path

import pytest

from klorb import klorb_init
from klorb import process_config as process_config_module
from klorb.klorb_init import InitError
from klorb.klorb_init import StepResult
from klorb.klorb_init import config_target_path
from klorb.klorb_init import create_symlink
from klorb.klorb_init import default_scope
from klorb.klorb_init import run_init
from klorb.klorb_init import symlink_target_dir
from klorb.klorb_init import template_config_text
from klorb.klorb_init import write_config_file


def test_default_scope_is_user_when_not_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert default_scope() == "user"


def test_default_scope_is_system_when_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert default_scope() == "system"


def test_config_target_path_user_scope_uses_user_config_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(process_config_module, "KLORB_CONFIG_DIR", tmp_path)
    assert config_target_path("user") == tmp_path / "klorb-config.json"


def test_config_target_path_system_scope_uses_etc_config_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    custom_etc_path = tmp_path / "etc" / "klorb-config.json"
    monkeypatch.setenv(process_config_module.KLORB_ETC_CONFIG_ENV_VAR, str(custom_etc_path))
    assert config_target_path("system") == custom_etc_path


def test_symlink_target_dir_system_is_usr_bin() -> None:
    assert symlink_target_dir("system") == Path("/usr/bin")


def test_symlink_target_dir_user_is_local_bin_under_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert symlink_target_dir("user") == tmp_path / ".local" / "bin"


def test_template_config_text_is_a_valid_schema_enveloped_json_document() -> None:
    data = json.loads(template_config_text())
    assert data["schema"]["name"] == "klorb-config"


def test_write_config_file_creates_parent_dir_and_writes_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    target = tmp_path / "nested" / "klorb-config.json"
    monkeypatch.setattr(klorb_init, "config_target_path", lambda scope: target)

    result = write_config_file("user", force=False)

    assert target.read_text(encoding="utf-8") == template_config_text()
    assert result.messages == [f"Wrote klorb config file to {target}."]


def test_write_config_file_refuses_to_overwrite_without_force(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    target = tmp_path / "klorb-config.json"
    target.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(klorb_init, "config_target_path", lambda scope: target)

    result = write_config_file("user", force=False)

    assert target.read_text(encoding="utf-8") == "existing"
    assert "already exists" in result.messages[0]


def test_write_config_file_overwrites_with_force(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    target = tmp_path / "klorb-config.json"
    target.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(klorb_init, "config_target_path", lambda scope: target)

    result = write_config_file("user", force=True)

    assert target.read_text(encoding="utf-8") == template_config_text()
    assert any("Overwriting existing config file" in message for message in result.messages)
    assert any("Wrote klorb config file" in message for message in result.messages)


def test_write_config_file_raises_init_error_on_os_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(klorb_init, "config_target_path", lambda scope: blocker / "klorb-config.json")

    with pytest.raises(InitError):
        write_config_file("user", force=False)


def test_create_symlink_creates_bin_dir_and_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    launcher = tmp_path / "launcher"
    launcher.write_text("#!/bin/sh", encoding="utf-8")
    monkeypatch.setattr(klorb_init, "symlink_target_dir", lambda scope: bin_dir)
    monkeypatch.setattr(klorb_init.sys, "argv", [str(launcher)])

    result = create_symlink("user", force=False)

    link = bin_dir / "klorb"
    assert link.is_symlink()
    assert link.resolve() == launcher.resolve()
    assert result.messages == [f"Created klorb executable symlink at {link} -> {launcher.resolve()}."]


def test_create_symlink_refuses_to_overwrite_without_force(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    existing_target = tmp_path / "something-else"
    existing_target.write_text("x", encoding="utf-8")
    link = bin_dir / "klorb"
    link.symlink_to(existing_target)
    monkeypatch.setattr(klorb_init, "symlink_target_dir", lambda scope: bin_dir)

    result = create_symlink("user", force=False)

    assert link.resolve() == existing_target.resolve()
    assert "already exists" in result.messages[0]


def test_create_symlink_overwrites_with_force(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    old_target = tmp_path / "old"
    old_target.write_text("x", encoding="utf-8")
    link = bin_dir / "klorb"
    link.symlink_to(old_target)
    new_launcher = tmp_path / "new-launcher"
    new_launcher.write_text("x", encoding="utf-8")
    monkeypatch.setattr(klorb_init, "symlink_target_dir", lambda scope: bin_dir)
    monkeypatch.setattr(klorb_init.sys, "argv", [str(new_launcher)])

    result = create_symlink("user", force=True)

    assert link.resolve() == new_launcher.resolve()
    assert any("Overwriting existing symlink" in message for message in result.messages)


def test_create_symlink_raises_init_error_when_bin_dir_cannot_be_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(klorb_init, "symlink_target_dir", lambda scope: blocker / "bin")

    with pytest.raises(InitError):
        create_symlink("user", force=False)


def test_run_init_refuses_system_scope_when_not_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)

    with pytest.raises(InitError, match="root"):
        run_init("system", force=False)


def test_run_init_runs_config_then_symlink_and_combines_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    call_order: list[str] = []

    def _fake_write_config_file(scope: str, *, force: bool) -> StepResult:
        call_order.append("config")
        return StepResult(["config-msg"])

    def _fake_create_symlink(scope: str, *, force: bool) -> StepResult:
        call_order.append("symlink")
        return StepResult(["symlink-msg"])

    monkeypatch.setattr(klorb_init, "write_config_file", _fake_write_config_file)
    monkeypatch.setattr(klorb_init, "create_symlink", _fake_create_symlink)

    messages = run_init("user", force=False)

    assert call_order == ["config", "symlink"]
    assert messages == ["config-msg", "symlink-msg"]


def test_run_init_stops_at_first_error_without_attempting_symlink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symlink_called: list[bool] = []

    def _fail(scope: str, *, force: bool) -> StepResult:
        raise InitError("boom")

    def _fake_create_symlink(scope: str, *, force: bool) -> StepResult:
        symlink_called.append(True)
        return StepResult([])

    monkeypatch.setattr(klorb_init, "write_config_file", _fail)
    monkeypatch.setattr(klorb_init, "create_symlink", _fake_create_symlink)

    with pytest.raises(InitError, match="boom"):
        run_init("user", force=False)

    assert symlink_called == []
