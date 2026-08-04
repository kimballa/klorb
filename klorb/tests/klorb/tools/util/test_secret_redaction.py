# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.secret_redaction.SecretRedactor."""

from pathlib import Path

from klorb.permissions.directory_access import DirRules
from klorb.session import Session, SessionConfig
from klorb.tools.util import SecretRedactor
from klorb.workspace import Workspace

_AWS_KEY = "AKIAABCDEFGHIJKLMNOP"


def _session(tmp_path: Path) -> Session:
    config = SessionConfig(
        workspace=Workspace(path=tmp_path), read_dirs=DirRules(), write_dirs=DirRules())
    return Session(config=config)


def test_redact_masks_a_known_credential_shape(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        redacted = SecretRedactor().redact(session, f"AWS_ACCESS_KEY_ID={_AWS_KEY}")
        assert _AWS_KEY not in redacted
        assert redacted.startswith("AWS_ACCESS_KEY_ID=[[SECRET:")
    finally:
        session.close()


def test_redact_leaves_ordinary_text_untouched() -> None:
    text = "def foo():\n    return 1 + 1\n"
    assert SecretRedactor().redact(None, text) == text


def test_redact_empty_text_is_a_no_op() -> None:
    assert SecretRedactor().redact(None, "") == ""


def test_same_secret_gets_the_same_token_across_separate_calls(tmp_path: Path) -> None:
    """A re-read of the same file (a later ReadFile call) must resolve the same token, not a
    fresh one, so a model's earlier reference to it stays valid."""
    session = _session(tmp_path)
    try:
        redactor = SecretRedactor()
        first = redactor.redact(session, _AWS_KEY)
        second = redactor.redact(session, _AWS_KEY)
        assert first == second
    finally:
        session.close()


def test_token_is_shared_across_separate_secretredactor_instances_on_the_same_session(
    tmp_path: Path,
) -> None:
    """SecretRedactor holds no state of its own -- the map lives in session.tool_state -- so a
    fresh instance (e.g. EditFileTool's own self._secret_redactor) resolves the same token
    ReadFileTool's instance minted earlier."""
    session = _session(tmp_path)
    try:
        redacted = SecretRedactor().redact(session, _AWS_KEY)
        assert SecretRedactor().detokenize(session, redacted) == _AWS_KEY
    finally:
        session.close()


def test_detokenize_round_trips_through_session_state(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        redactor = SecretRedactor()
        text = f"AWS_ACCESS_KEY_ID={_AWS_KEY}\nordinary line"
        redacted = redactor.redact(session, text)
        assert redactor.detokenize(session, redacted) == text
    finally:
        session.close()


def test_detokenize_leaves_unknown_token_untouched() -> None:
    text = "[[SECRET:aws_access_key:000000000000]]"
    assert SecretRedactor().detokenize(None, text) == text


def test_detokenize_leaves_text_without_a_token_untouched(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        text = "nothing secret here"
        assert SecretRedactor().detokenize(session, text) == text
    finally:
        session.close()


def test_none_session_redacts_without_a_persistent_map() -> None:
    """A None session (e.g. a ToolSetupContext built directly, as most unit tests do) still
    redacts -- it just can't remember the token past that one call, so a later detokenize()
    call with no session can't reverse it."""
    redactor = SecretRedactor()
    redacted = redactor.redact(None, _AWS_KEY)
    assert _AWS_KEY not in redacted
    assert redactor.detokenize(None, redacted) == redacted


def test_token_map_lives_only_in_session_tool_state_never_elsewhere(tmp_path: Path) -> None:
    """The token<->plaintext map must be reachable only through session.tool_state -- the field
    Session documents as never read/written by Session itself and never persisted to disk (see
    docs/specs/secret-redaction.md's "Session-state storage" section) -- not copied anywhere
    else a session-persistence path could pick it up."""
    session = _session(tmp_path)
    try:
        SecretRedactor().redact(session, _AWS_KEY)
        assert "SecretRedaction" in session.tool_state
        assert _AWS_KEY in session.tool_state["SecretRedaction"]["token_to_secret"].values()
    finally:
        session.close()
