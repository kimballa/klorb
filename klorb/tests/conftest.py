# © Copyright 2026 Aaron Kimball
"""Shared pytest fixtures for klorb's test suite."""

from pathlib import Path

import pytest

from klorb import logging_config
from klorb import process_config as process_config_module
from klorb.token_estimate import configure_tiktoken_cache_env


@pytest.fixture(autouse=True)
def _redirect_session_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from writing session log files into the real KLORB_STATE_DIR."""
    monkeypatch.setattr(logging_config, "SESSION_LOGS_DIR", tmp_path / "session-logs")


@pytest.fixture(autouse=True)
def _redirect_tool_calls_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from writing tool-calls.log into the real KLORB_STATE_DIR."""
    monkeypatch.setattr("klorb.tool_call_log.KLORB_STATE_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _isolate_config_layers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point every file-backed config layer at empty locations under `tmp_path`, and blank out
    the packaged built-in-defaults layer, so no test anywhere in the suite can accidentally read
    a real `/etc/klorb/klorb-config.json`, the developer's own `~/.config/klorb/klorb-config.json`,
    or the packaged `default-config.json`'s own `readDirs`/session-default values via
    `load_process_config()`. Tests that specifically cover the packaged layer (see
    `test_default_config_layer_*` in test_process_config.py) restore the real
    `_default_config_layer` themselves.
    """
    monkeypatch.setenv(
        process_config_module.KLORB_ETC_CONFIG_ENV_VAR, str(tmp_path / "etc" / "klorb-config.json"))
    monkeypatch.setattr(process_config_module, "KLORB_CONFIG_DIR", tmp_path / "user-config")
    monkeypatch.setattr(process_config_module, "_default_config_layer", lambda warnings: {})


@pytest.fixture(autouse=True)
def _isolate_user_models_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point `ModelRegistry`'s user-override tier (`klorb.models.registry.KLORB_DATA_DIR`) at
    an empty temp dir, so no test anywhere in the suite can accidentally pick up a real
    `~/.local/share/klorb/models` file from the machine running the suite.
    """
    monkeypatch.setattr("klorb.models.registry.KLORB_DATA_DIR", tmp_path / "klorb-data-dir")


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
