# © Copyright 2026 Aaron Kimball
"""Shared pytest fixtures for klorb's test suite."""

import glob
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from klorb import logging_config
from klorb import process_config as process_config_module
from klorb.token_estimate import configure_tiktoken_cache_env
from klorb.tools.skill import catalog as skill_catalog
from klorb.tools.skill import common as skill_common


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


@pytest.fixture(autouse=True)
def _reset_skill_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the internal (packaged) skill tier at an empty temp dir and reset the process-wide
    skill catalog (`klorb.tools.skill.catalog`) before each test, so no test anywhere in the suite
    accidentally discovers klorb's packaged skills (e.g. create-edit-skill, which is discoverable
    regardless of workspace trust) or reuses a catalog an earlier test's workspace already built
    -- the catalog is process-wide and normally built exactly once, so without this reset every
    test after the first in a given process would see the first test's workspace's skills. Mirrors
    `_isolate_config_layers`'s blanking of the packaged config layer. Tests that specifically cover
    skill discovery/catalog behavior restore the real internal tier and/or rebuild the catalog
    themselves (see test_skills_session.py, test_skill_common.py).
    """
    skill_catalog.get_skill_catalog_registry().reset_for_tests()
    monkeypatch.setattr(skill_common, "internal_skills_dir", lambda: tmp_path / "empty-internal-skills")
    monkeypatch.setattr(skill_common, "KLORB_DATA_DIR", tmp_path / "empty-user-skills-data")


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


@pytest.fixture(autouse=True)
def _skip_session_naming(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out the session-naming classifier's network round trip for every `Session.send_turn()`
    call in the suite, so a test's mocked `ApiProvider` isn't also asked to answer the naming
    classifier's request on a session's first turn -- it would otherwise see extra
    `send_prompt()` calls (and, on a generic mock, a bogus reply that fails to parse as
    `SessionName` and retries once) it never expected, on top of whatever the test itself set up.
    Returns `None`, as if naming always failed, which is exactly `generate_session_name`'s own
    "never raises" contract for any failure. `Session` naming behavior itself is covered by
    naming-specific tests (see test_session.py), which override this fixture's patch locally with
    a real fake implementation.
    """
    monkeypatch.setattr(
        "klorb.session.mixins.core.generate_session_name", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def _cleanup_scratchpad_dirs() -> Iterator[None]:
    """Remove any `klorb-scratchpad-*` temp dirs created during a test.

    Tests that create a `Session` or `Scratchpad` without calling `close()`/`cleanup()`
    leave behind a `tempfile.mkdtemp(prefix="klorb-scratchpad-")` directory. The ``atexit``
    backstop registered at creation time only fires when the *process* exits, so without
    per-test cleanup these directories accumulate across the test suite run.  This fixture
    snapshots the existing dirs before the test runs, then removes only the new ones that
    appeared during it — leaving any scratchpad belonging to a live session untouched.
    """
    before = set(glob.glob("/tmp/klorb-scratchpad-*"))
    yield
    for d in glob.glob("/tmp/klorb-scratchpad-*"):
        if d not in before:
            shutil.rmtree(d, ignore_errors=True)
