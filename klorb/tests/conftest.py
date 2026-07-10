# © Copyright 2026 Aaron Kimball
"""Shared pytest fixtures for klorb's test suite."""

from pathlib import Path

import pytest

from klorb import logging_config
from klorb.token_estimate import configure_tiktoken_cache_env


@pytest.fixture(autouse=True)
def _redirect_session_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from writing session log files into the real KLORB_STATE_DIR."""
    monkeypatch.setattr(logging_config, "SESSION_LOGS_DIR", tmp_path / "session-logs")


@pytest.fixture(scope="session", autouse=True)
def _configure_tiktoken_cache_dir() -> None:
    """Point tiktoken at the machine's `klorb init`-installed bundled cache, if there is one,
    the same way `klorb.cli.main()` does at process start (see `configure_tiktoken_cache_env`)
    -- so the first test in the session that estimates a token count (`klorb.token_estimate.
    estimate_tokens()`, via `tiktoken.get_encoding()`) reads it from disk instead of needing
    network access. A no-op on a machine that hasn't run `klorb init`, same as production.
    Session-scoped (rather than per test) since it only needs to run once, before whichever
    test first touches tiktoken; individual tests that exercise `configure_tiktoken_cache_env`
    itself (see test_token_estimate.py) manage `$TIKTOKEN_CACHE_DIR` and `$KLORB_DATA_DIR`
    themselves via `monkeypatch`, which restores this fixture's value once each such test ends.
    """
    configure_tiktoken_cache_env()


@pytest.fixture(autouse=True)
def _fast_pilot_idle_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink Textual's `wait_for_idle()` sleep (`textual._wait.SLEEP_GRANULARITY`, normally
    20ms) to 5ms, since every `Pilot.press`/`pilot.pause()` call in any Textual-driven test in
    this suite burns at least one such sleep of real wall-clock time regardless of what klorb's
    own code does with the simulated input.
    """
    monkeypatch.setattr("textual._wait.SLEEP_GRANULARITY", 0.005)
