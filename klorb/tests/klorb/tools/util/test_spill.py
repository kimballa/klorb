# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.spill.SpillDir."""

from pathlib import Path
from typing import Callable

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.session import Session, SessionConfig
from klorb.tools.util import SpillDir
from klorb.tools.util import spill as spill_module
from klorb.workspace import Workspace


def _session(tmp_path: Path, make_session_config: Callable[..., SessionConfig]) -> Session:
    config = make_session_config(
        workspace=Workspace(path=tmp_path), read_dirs=DirRules(), write_dirs=DirRules())
    return Session(config=config)


def test_get_or_create_makes_a_directory(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    try:
        tmpdir = SpillDir("Grep").get_or_create(session)
        assert tmpdir.is_dir()
        assert tmpdir.name.startswith("klorb-grep-")
    finally:
        session.close()


def test_get_or_create_reuses_the_same_directory_within_a_session(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    try:
        spill_dir = SpillDir("Grep")
        first = spill_dir.get_or_create(session)
        second = spill_dir.get_or_create(session)
        assert first == second
    finally:
        session.close()


def test_distinct_tool_names_get_distinct_directories(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    try:
        grep_dir = SpillDir("Grep").get_or_create(session)
        web_fetch_dir = SpillDir("WebFetch").get_or_create(session)
        assert grep_dir != web_fetch_dir
    finally:
        session.close()


def test_session_close_removes_the_spill_directory(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    tmpdir = SpillDir("Grep").get_or_create(session)
    assert tmpdir.is_dir()

    session.close()

    assert not tmpdir.exists()


def test_grant_read_access_adds_tmpdir_to_read_dirs_allow(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    try:
        spill_dir = SpillDir("Grep")
        tmpdir = spill_dir.get_or_create(session)

        spill_dir.grant_read_access(session, tmpdir)

        assert tmpdir in session.config.read_dirs.allow
    finally:
        session.close()


def test_session_close_unregisters_the_atexit_backstop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    """`session.close()` runs the spill tmpdir's teardown eagerly; that teardown must unregister
    its `atexit` backstop so the same cleanup (and its "Cleaned up ... tmpdir" log line) doesn't
    fire a second time when the interpreter actually exits."""
    session = _session(tmp_path, make_session_config)
    registered: list[Callable[[], None]] = []
    unregistered: list[Callable[[], None]] = []
    monkeypatch.setattr(spill_module.atexit, "register", registered.append)
    monkeypatch.setattr(spill_module.atexit, "unregister", unregistered.append)

    tmpdir = SpillDir("Grep").get_or_create(session)
    assert len(registered) == 1

    session.close()

    assert not tmpdir.exists()
    # `session.close()` also runs the session's other teardowns (e.g. its `Scratchpad`'s own
    # atexit-backed cleanup, which -- since `atexit` is one shared module object -- shows up
    # through this same monkeypatch too), so check specifically for the spill callback rather
    # than requiring `unregistered` to contain nothing else.
    assert registered[0] in unregistered


def test_grant_read_access_is_idempotent(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    try:
        spill_dir = SpillDir("Grep")
        tmpdir = spill_dir.get_or_create(session)

        spill_dir.grant_read_access(session, tmpdir)
        spill_dir.grant_read_access(session, tmpdir)

        assert session.config.read_dirs.allow.count(tmpdir) == 1
    finally:
        session.close()
