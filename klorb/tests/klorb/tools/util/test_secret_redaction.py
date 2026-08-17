# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.secret_redaction.SecretRedactor."""
import json
from collections.abc import Callable
from pathlib import Path

from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME, DirRules
from klorb.session import Session, SessionConfig
from klorb.tools.util import SecretRedactor, get_or_create_secret_redactor
from klorb.tools.util.secret_redaction import load_secrets_baseline
from klorb.workspace import Workspace

_AWS_KEY = "AKIAABCDEFGHIJKLMNOP"


def _session(tmp_path: Path, make_session_config: Callable[..., SessionConfig]) -> Session:
    config = make_session_config(
        workspace=Workspace(path=tmp_path, trusted=True), read_dirs=DirRules(), write_dirs=DirRules())
    return Session(config=config)


def test_redact_masks_a_known_credential_shape(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
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


def test_same_secret_gets_the_same_token_across_separate_calls(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A re-read of the same file (a later ReadFile call) must resolve the same token, not a
    fresh one, so a model's earlier reference to it stays valid."""
    session = _session(tmp_path, make_session_config)
    try:
        redactor = SecretRedactor()
        first = redactor.redact(session, _AWS_KEY)
        second = redactor.redact(session, _AWS_KEY)
        assert first == second
    finally:
        session.close()


def test_token_is_shared_across_separate_secretredactor_instances_on_the_same_session(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """SecretRedactor holds no state of its own -- the map lives in session.tool_state -- so a
    fresh instance (e.g. EditFileTool's own self._secret_redactor) resolves the same token
    ReadFileTool's instance minted earlier."""
    session = _session(tmp_path, make_session_config)
    try:
        redacted = SecretRedactor().redact(session, _AWS_KEY)
        assert SecretRedactor().detokenize(session, redacted) == _AWS_KEY
    finally:
        session.close()


def test_detokenize_round_trips_through_session_state(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
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


def test_detokenize_leaves_text_without_a_token_untouched(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
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


def test_token_map_lives_only_in_session_tool_state_never_elsewhere(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """The token<->plaintext map must be reachable only through session.tool_state -- the field
    Session documents as never read/written by Session itself and never persisted to disk (see
    docs/specs/secret-redaction.md's "Session-state storage" section) -- not copied anywhere
    else a session-persistence path could pick it up."""
    session = _session(tmp_path, make_session_config)
    try:
        SecretRedactor().redact(session, _AWS_KEY)
        assert "SecretRedaction" in session.tool_state
        assert _AWS_KEY in session.tool_state["SecretRedaction"]["token_to_secret"].values()
    finally:
        session.close()


def _write_baseline(tmp_path: Path, hashes: list[str]) -> None:
    """Write a minimal `.klorb/secrets-baseline.json` in `detect-secrets` baseline format."""
    klorb_dir = tmp_path / KLORB_PROJECT_DIR_NAME
    klorb_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {
        "version": "1.5.0",
        "plugins_used": [],
        "filters_used": [],
        "results": {
            "test-fixture.txt": [{"type": "AWS Access Key", "hashed_secret": h} for h in hashes],
        },
    }
    (klorb_dir / "secrets-baseline.json").write_text(json.dumps(data), encoding="utf-8")


def test_load_secrets_baseline_returns_hashes(tmp_path: Path) -> None:
    hashes = ["aaa", "bbb"]
    _write_baseline(tmp_path, hashes)
    result = load_secrets_baseline(tmp_path, trusted=True)
    assert result == frozenset({"aaa", "bbb"})


def test_load_secrets_baseline_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert load_secrets_baseline(tmp_path, trusted=True) == frozenset()


def test_load_secrets_baseline_returns_empty_on_malformed_json(tmp_path: Path) -> None:
    klorb_dir = tmp_path / KLORB_PROJECT_DIR_NAME
    klorb_dir.mkdir(parents=True, exist_ok=True)
    (klorb_dir / "secrets-baseline.json").write_text("not json", encoding="utf-8")
    assert load_secrets_baseline(tmp_path, trusted=True) == frozenset()


def test_load_secrets_baseline_returns_empty_when_results_missing(tmp_path: Path) -> None:
    klorb_dir = tmp_path / KLORB_PROJECT_DIR_NAME
    klorb_dir.mkdir(parents=True, exist_ok=True)
    (klorb_dir / "secrets-baseline.json").write_text(json.dumps({
        "version": "1.5.0",
        "plugins_used": [],
        "filters_used": [],
    }), encoding="utf-8")
    assert load_secrets_baseline(tmp_path, trusted=True) == frozenset()


def test_load_secrets_baseline_returns_empty_when_untrusted(tmp_path: Path) -> None:
    """An untrusted workspace must never load a baseline -- it could be attacker-controlled."""
    _write_baseline(tmp_path, ["aaa", "bbb"])
    assert load_secrets_baseline(tmp_path, trusted=False) == frozenset()


def test_baseline_hash_skips_redaction() -> None:
    """A secret whose SHA-1 hash appears in `baseline_hashes` is left unredacted."""
    import hashlib
    secret_value = "AKIAABCDEFGHIJKLMNOP"
    sha1 = hashlib.sha1(secret_value.encode("utf-8")).hexdigest()
    redactor = SecretRedactor(baseline_hashes=frozenset({sha1}))
    text = f"AWS_ACCESS_KEY_ID={secret_value}"
    assert redactor.redact(None, text) == text


def test_baseline_does_not_skip_non_baseline_secrets() -> None:
    """Secrets not in the baseline are still redacted even when a baseline is loaded."""
    import hashlib
    secret_value = "AKIAABCDEFGHIJKLMNOP"
    unrelated_hash = hashlib.sha1(b"some-other-value").hexdigest()
    redactor = SecretRedactor(baseline_hashes=frozenset({unrelated_hash}))
    text = f"AWS_ACCESS_KEY_ID={secret_value}"
    assert secret_value not in redactor.redact(None, text)


def test_empty_baseline_redacts_normally() -> None:
    """An empty baseline (the default) does not change redaction behavior."""
    redactor = SecretRedactor()
    assert redactor.redact(None, _AWS_KEY) != _AWS_KEY


def test_get_or_create_caches_redactor_in_session_tool_state(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """`get_or_create_secret_redactor` caches the `SecretRedactor` instance in
    `session.tool_state` so subsequent calls return the same object."""
    session = _session(tmp_path, make_session_config)
    try:
        first = get_or_create_secret_redactor(session)
        second = get_or_create_secret_redactor(session)
        assert first is second
        assert "SecretRedaction" in session.tool_state
        assert session.tool_state["SecretRedaction"]["redactor"] is first
    finally:
        session.close()


def test_get_or_create_returns_fresh_instance_when_session_is_none() -> None:
    """A `None` session (unit tests with no real Session) returns a fresh uncached instance."""
    first = get_or_create_secret_redactor(None)
    second = get_or_create_secret_redactor(None)
    assert first is not second


def test_get_or_create_uses_baseline_hashes(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """The cached redactor applies baseline hashes the same as a manually-constructed one."""
    import hashlib
    secret_value = "AKIAABCDEFGHIJKLMNOP"
    sha1 = hashlib.sha1(secret_value.encode("utf-8")).hexdigest()
    _write_baseline(tmp_path, [sha1])
    session = _session(tmp_path, make_session_config)
    try:
        redactor = get_or_create_secret_redactor(session)
        text = f"AWS_ACCESS_KEY_ID={secret_value}"
        assert redactor.redact(session, text) == text
    finally:
        session.close()
