# © Copyright 2026 Aaron Kimball
"""Shared pytest fixtures for klorb's test suite."""

import glob
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from klorb import logging_config
from klorb import process_config as process_config_module
from klorb.session import SessionConfig
from klorb.token_estimate import configure_tiktoken_cache_env
from klorb.tools.skill import common as skill_common
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def _redirect_session_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from writing session log files into the real KLORB_STATE_DIR."""
    monkeypatch.setattr(logging_config, "get_session_logs_dir", lambda: tmp_path / "session-logs")


@pytest.fixture(autouse=True)
def _redirect_tool_calls_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from writing tool-calls.log into the real KLORB_STATE_DIR."""
    monkeypatch.setattr("klorb.tool_call_log.get_klorb_state_dir", lambda: tmp_path)


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
    monkeypatch.setattr(process_config_module, "get_klorb_config_dir", lambda: tmp_path / "user-config")
    monkeypatch.setattr(
        process_config_module, "_default_config_layer",
        lambda warnings: process_config_module.LoadedConfigLayer(
            contents={}, text="", source_label="blanked-default-config"))


@pytest.fixture(autouse=True)
def _isolate_user_models_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point `ModelRegistry`'s user-override tier (`klorb.models.registry.get_klorb_data_dir()`) at
    an empty temp dir, so no test anywhere in the suite can accidentally pick up a real
    `~/.local/share/klorb/models` file from the machine running the suite.
    """
    monkeypatch.setattr("klorb.models.registry.get_klorb_data_dir", lambda: tmp_path / "klorb-data-dir")


@pytest.fixture(autouse=True)
def _isolate_session_persistence_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point every per-project data path (`klorb.workspace.input_history.project_history_dir`,
    which `klorb.workspace.session_store` and `klorb.workspace.trust_manager.projects_path`'s
    default both build on) at an empty temp dir, so a `Session` with a `trusted=True` workspace
    -- common across the test suite, e.g. for exercising permission logic that only applies to a
    trusted workspace -- can't write real `session.json`/`sessions.json`/`session.lock` files
    into the developer's own `~/.local/share/klorb/` merely by calling `send_turn()`
    (`SessionPersistenceMixin.claim_session_directory`). Tests that specifically exercise
    persistence against a real (if temporary) directory layout construct their own
    `TrustManager(path=...)` and additionally patch `klorb.workspace.input_history.
    get_klorb_data_dir` themselves (see `tui.conftest._isolated_data_dir`), which simply overrides
    this default with a different `tmp_path`-rooted location.
    """
    isolated = tmp_path / "klorb-session-data-dir"
    monkeypatch.setattr("klorb.workspace.input_history.get_klorb_data_dir", lambda: isolated)
    monkeypatch.setattr("klorb.workspace.trust_manager.get_klorb_data_dir", lambda: isolated)


@pytest.fixture(autouse=True)
def _reset_skill_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the internal (packaged) skill tier at an empty temp dir before each test, so no test
    anywhere in the suite accidentally discovers klorb's packaged skills (e.g. create-edit-skill,
    which is discoverable regardless of workspace trust). Each `Session`/`SkillCatalogRegistry`
    is its own fresh instance (see `klorb.tools.skill.catalog`), so there's no process-wide
    catalog state to reset between tests -- only the on-disk tier locations need isolating.
    Mirrors `_isolate_config_layers`'s blanking of the packaged config layer.
    """
    monkeypatch.setattr(skill_common, "internal_skills_dir", lambda: tmp_path / "empty-internal-skills")
    monkeypatch.setattr(skill_common, "get_klorb_data_dir", lambda: tmp_path / "empty-user-skills-data")


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
def _isolate_embedding_model_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point `klorb.search_index.embedding.embedding_model_target_dir()` at an empty temp dir, so
    `embedding_model_available()` is always `False` in the suite and `Session.
    _create_workspace_indexer` never constructs a real `WorkspaceIndexer` for a trusted-workspace
    `SessionConfig()`."""
    monkeypatch.setattr(
        "klorb.search_index.embedding.get_klorb_data_dir", lambda: tmp_path / "embedding-model-dir")


@pytest.fixture(autouse=True)
def _reset_global_memory_indexer() -> None:
    """Reset the process-wide `memories-global` catalog `MemoryCatalogIndexer` singleton between
    tests, so one test's construction never leaks into the next."""
    from klorb.search_index.memory_indexer import reset_global_memory_indexer_for_testing
    reset_global_memory_indexer_for_testing()


@pytest.fixture
def make_session_config(tmp_path: Path) -> Callable[..., SessionConfig]:
    """Factory for a `SessionConfig` rooted at this test's own `tmp_path`, so a test that needs a
    customized `SessionConfig` doesn't have to restate `workspace=Workspace(path=tmp_path)` for
    every call; any field, including `workspace` itself, can still be overridden by keyword."""
    def _make(**overrides: Any) -> SessionConfig:
        overrides.setdefault("workspace", Workspace(path=tmp_path))
        return SessionConfig(**overrides)
    return _make


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
