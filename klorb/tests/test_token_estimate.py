# © Copyright 2026 Aaron Kimball
"""Tests for klorb.token_estimate."""

import logging
import os
from pathlib import Path

import pytest
import tiktoken

from klorb import token_estimate
from klorb.token_estimate import (
    ENCODING_NAME,
    TIKTOKEN_CACHE_DIR_ENV_VAR,
    configure_tiktoken_cache_env,
    estimate_tokens,
    install_tiktoken_cache,
    tiktoken_cache_encoding_dir,
    tiktoken_cache_target_dir,
)


def test_estimate_tokens_empty_string_is_zero() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_matches_the_underlying_encoding() -> None:
    text = "hello there, this is a test message with several words in it."
    encoding = tiktoken.get_encoding(ENCODING_NAME)
    assert estimate_tokens(text) == len(encoding.encode(text))


def test_estimate_tokens_does_not_raise_on_special_token_like_substrings() -> None:
    assert estimate_tokens("<|endoftext|>") > 0


def test_tiktoken_cache_target_dir_is_under_klorb_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_estimate, "KLORB_DATA_DIR", tmp_path)
    assert tiktoken_cache_target_dir() == tmp_path / "tiktoken-cache"


def test_tiktoken_cache_encoding_dir_is_encoding_name_subdir_of_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_estimate, "KLORB_DATA_DIR", tmp_path)
    assert tiktoken_cache_encoding_dir() == tmp_path / "tiktoken-cache" / ENCODING_NAME


def test_install_tiktoken_cache_copies_packaged_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_estimate, "KLORB_DATA_DIR", tmp_path)

    messages = install_tiktoken_cache()

    encoding_dir = tmp_path / "tiktoken-cache" / ENCODING_NAME
    assert encoding_dir.is_dir()
    assert (encoding_dir / "README.cache").is_file()
    cache_files = [p for p in encoding_dir.iterdir() if p.name != "README.cache"]
    assert len(cache_files) == 1
    assert messages == [f"Copied tiktoken cache to {tmp_path / 'tiktoken-cache'}."]


def test_install_tiktoken_cache_is_safe_to_call_more_than_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_estimate, "KLORB_DATA_DIR", tmp_path)

    install_tiktoken_cache()
    install_tiktoken_cache()

    encoding_dir = tmp_path / "tiktoken-cache" / ENCODING_NAME
    assert (encoding_dir / "README.cache").is_file()


def test_configure_tiktoken_cache_env_noop_when_cache_not_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_estimate, "KLORB_DATA_DIR", tmp_path)
    monkeypatch.delenv(TIKTOKEN_CACHE_DIR_ENV_VAR, raising=False)

    configure_tiktoken_cache_env()

    assert TIKTOKEN_CACHE_DIR_ENV_VAR not in os.environ


def test_configure_tiktoken_cache_env_sets_env_var_when_cache_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_estimate, "KLORB_DATA_DIR", tmp_path)
    monkeypatch.delenv(TIKTOKEN_CACHE_DIR_ENV_VAR, raising=False)
    install_tiktoken_cache()

    configure_tiktoken_cache_env()

    encoding_dir = tmp_path / "tiktoken-cache" / ENCODING_NAME
    assert os.environ[TIKTOKEN_CACHE_DIR_ENV_VAR] == str(encoding_dir)


def test_configure_tiktoken_cache_env_logs_when_cache_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(token_estimate, "KLORB_DATA_DIR", tmp_path)
    monkeypatch.delenv(TIKTOKEN_CACHE_DIR_ENV_VAR, raising=False)
    install_tiktoken_cache()

    with caplog.at_level(logging.INFO, logger="klorb.token_estimate"):
        configure_tiktoken_cache_env()

    assert any(TIKTOKEN_CACHE_DIR_ENV_VAR in record.getMessage() for record in caplog.records)
