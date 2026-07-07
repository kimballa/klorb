# © Copyright 2026 Aaron Kimball
"""Tests for klorb.sandbox: the bwrap availability smoke test and its cache. See
docs/plans/ready/004-bash-permissions-and-bash-tool.md's "Fallback when bwrap can't actually
sandbox anything" section.
"""

import subprocess
from collections.abc import Iterator

import pytest

from klorb import sandbox


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    sandbox.reset_availability_cache()
    yield
    sandbox.reset_availability_cache()


def test_unavailable_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    assert sandbox.bwrap_available() is False
    assert sandbox.detect_bwrap_unavailable_reason() == "missing_binary"


def test_unavailable_when_smoke_test_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        sandbox.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode=1))
    assert sandbox.bwrap_available() is False
    assert sandbox.detect_bwrap_unavailable_reason() == "no_userns"


def test_available_when_binary_present_and_smoke_test_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        sandbox.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode=0))
    assert sandbox.bwrap_available() is True
    assert sandbox.detect_bwrap_unavailable_reason() is None


def test_result_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_which(name: str) -> str:
        calls["n"] += 1
        return "/usr/bin/bwrap"

    monkeypatch.setattr(sandbox.shutil, "which", fake_which)
    monkeypatch.setattr(
        sandbox.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode=0))

    assert sandbox.bwrap_available() is True
    assert sandbox.bwrap_available() is True
    assert calls["n"] == 1


def test_reset_cache_forces_a_fresh_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    assert sandbox.bwrap_available() is False

    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        sandbox.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode=0))
    assert sandbox.bwrap_available() is False  # still cached

    sandbox.reset_availability_cache()
    assert sandbox.bwrap_available() is True


def test_build_bwrap_argv_is_a_stub() -> None:
    with pytest.raises(NotImplementedError):
        sandbox.build_bwrap_argv()
