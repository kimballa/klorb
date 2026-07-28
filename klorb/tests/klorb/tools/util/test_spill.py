# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.spill.SpillDir."""

from pathlib import Path

from klorb.permissions.directory_access import DirRules
from klorb.session import Session, SessionConfig
from klorb.tools.util import SpillDir
from klorb.workspace import Workspace


def _session(tmp_path: Path) -> Session:
    config = SessionConfig(
        workspace=Workspace(path=tmp_path), read_dirs=DirRules(), write_dirs=DirRules())
    return Session(config=config)


def test_get_or_create_makes_a_directory(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        tmpdir = SpillDir("Grep").get_or_create(session)
        assert tmpdir.is_dir()
        assert tmpdir.name.startswith("klorb-grep-")
    finally:
        session.close()


def test_get_or_create_reuses_the_same_directory_within_a_session(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        spill_dir = SpillDir("Grep")
        first = spill_dir.get_or_create(session)
        second = spill_dir.get_or_create(session)
        assert first == second
    finally:
        session.close()


def test_distinct_tool_names_get_distinct_directories(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        grep_dir = SpillDir("Grep").get_or_create(session)
        web_fetch_dir = SpillDir("WebFetch").get_or_create(session)
        assert grep_dir != web_fetch_dir
    finally:
        session.close()


def test_session_close_removes_the_spill_directory(tmp_path: Path) -> None:
    session = _session(tmp_path)
    tmpdir = SpillDir("Grep").get_or_create(session)
    assert tmpdir.is_dir()

    session.close()

    assert not tmpdir.exists()


def test_grant_read_access_adds_tmpdir_to_read_dirs_allow(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        spill_dir = SpillDir("Grep")
        tmpdir = spill_dir.get_or_create(session)

        spill_dir.grant_read_access(session, tmpdir)

        assert tmpdir in session.config.read_dirs.allow
    finally:
        session.close()


def test_grant_read_access_is_idempotent(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        spill_dir = SpillDir("Grep")
        tmpdir = spill_dir.get_or_create(session)

        spill_dir.grant_read_access(session, tmpdir)
        spill_dir.grant_read_access(session, tmpdir)

        assert session.config.read_dirs.allow.count(tmpdir) == 1
    finally:
        session.close()
