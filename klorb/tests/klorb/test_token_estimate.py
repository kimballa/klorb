# © Copyright 2026 Aaron Kimball
"""Tests for klorb.token_estimate."""

import logging
import os
from datetime import datetime
from pathlib import Path

import pytest
import tiktoken

from klorb import token_estimate
from klorb.message import Message, MessageFragment
from klorb.models.configured_model import ConfiguredModel
from klorb.token_estimate import (
    ENCODING_NAME,
    TIKTOKEN_CACHE_DIR_ENV_VAR,
    configure_tiktoken_cache_env,
    estimate_image_tokens,
    estimate_message_tokens,
    estimate_tokens,
    install_tiktoken_cache,
    tiktoken_cache_encoding_dir,
    tiktoken_cache_target_dir,
)


def _model(vision_details: dict[str, object] | None = None) -> ConfiguredModel:
    return ConfiguredModel(
        {"name": "test/model", "capabilities": {"vision": True, "vision_details": vision_details}},
        source="test")


class TestEstimateImageTokens:
    def test_anthropic_tiles_formula(self) -> None:
        assert estimate_image_tokens(750, 1000, _model({"token_formula": "anthropic_tiles"})) == 1000

    def test_qwen_pixel_ratio_formula(self) -> None:
        assert estimate_image_tokens(320, 320, _model({"token_formula": "qwen_pixel_ratio"})) == 102

    def test_openai_patch_budget_formula(self) -> None:
        model = _model({
            "token_formula": "openai_patch_budget", "patch_budget": 1536, "token_multiplier": 2.46,
        })
        # 320x320 -> 10x10 patches = 100 patches, well under the 1536 budget.
        assert estimate_image_tokens(320, 320, model) == round(100 * 2.46)

    def test_openai_patch_budget_formula_clamps_to_patch_budget(self) -> None:
        model = _model({
            "token_formula": "openai_patch_budget", "patch_budget": 10, "token_multiplier": 2.0,
        })
        assert estimate_image_tokens(3200, 3200, model) == 20

    def test_unrecognized_formula_falls_back_to_anthropic_tiles(self) -> None:
        assert estimate_image_tokens(750, 1000, _model({"token_formula": "moonshotai_unknown"})) == 1000

    def test_no_vision_details_falls_back_to_anthropic_tiles(self) -> None:
        model = ConfiguredModel({"name": "test/model", "capabilities": {"vision": True}}, source="test")
        assert estimate_image_tokens(750, 1000, model) == 1000


class TestEstimateMessageTokens:
    def _message(self, **overrides: object) -> Message:
        defaults: dict[str, object] = dict(
            content="hello", role="user", num_tokens=0, processing_state="complete",
            timestamp=datetime.now())
        defaults.update(overrides)
        return Message(**defaults)  # type: ignore[arg-type]

    def test_matches_estimate_tokens_when_no_fragments(self) -> None:
        message = self._message(content="hello world")
        assert estimate_message_tokens(message, _model()) == estimate_tokens("hello world")

    def test_sums_text_fragments_via_estimate_tokens(self) -> None:
        message = self._message(fragments=[
            MessageFragment(type="text", text="one"), MessageFragment(type="text", text="two"),
        ])
        assert estimate_message_tokens(message, _model()) == (
            estimate_tokens("one") + estimate_tokens("two"))

    def test_sums_image_fragment_via_estimate_image_tokens_using_resized_dimensions(self) -> None:
        model = _model({"token_formula": "anthropic_tiles"})
        message = self._message(fragments=[
            MessageFragment(type="text", text="look at this"),
            MessageFragment(
                type="image_url", image_url={"url": "data:image/png;base64,xx"},
                resized_width=750, resized_height=1000),
        ])
        assert estimate_message_tokens(message, model) == estimate_tokens("look at this") + 1000


def test_estimate_tokens_empty_string_is_zero() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_matches_the_underlying_encoding() -> None:
    text = "hello there, this is a test message with several words in it."
    encoding = tiktoken.get_encoding(ENCODING_NAME)
    assert estimate_tokens(text) == len(encoding.encode(text))


def test_estimate_tokens_does_not_raise_on_special_token_like_substrings() -> None:
    assert estimate_tokens("<|endoftext|>") > 0


def test_tool_token_counts_keys_local_tools_by_function_name() -> None:
    definitions = [
        {"type": "function", "function": {"name": "Echo", "description": "d", "parameters": {}}},
    ]
    counts = token_estimate.tool_token_counts(definitions)
    assert set(counts) == {"Echo"}
    assert counts["Echo"] > 0


def test_tool_token_counts_keys_server_tools_by_type() -> None:
    definitions = [
        {"type": "openrouter:web_search", "parameters": {"max_results": 10}},
    ]
    counts = token_estimate.tool_token_counts(definitions)
    assert set(counts) == {"openrouter:web_search"}
    assert counts["openrouter:web_search"] > 0


def test_tiktoken_cache_target_dir_is_under_klorb_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_estimate, "get_klorb_data_dir", lambda: tmp_path)
    assert tiktoken_cache_target_dir() == tmp_path / "tiktoken-cache"


def test_tiktoken_cache_encoding_dir_is_encoding_name_subdir_of_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_estimate, "get_klorb_data_dir", lambda: tmp_path)
    assert tiktoken_cache_encoding_dir() == tmp_path / "tiktoken-cache" / ENCODING_NAME


def test_install_tiktoken_cache_copies_packaged_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_estimate, "get_klorb_data_dir", lambda: tmp_path)

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
    monkeypatch.setattr(token_estimate, "get_klorb_data_dir", lambda: tmp_path)

    install_tiktoken_cache()
    install_tiktoken_cache()

    encoding_dir = tmp_path / "tiktoken-cache" / ENCODING_NAME
    assert (encoding_dir / "README.cache").is_file()


def test_configure_tiktoken_cache_env_noop_when_cache_not_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_estimate, "get_klorb_data_dir", lambda: tmp_path)
    monkeypatch.delenv(TIKTOKEN_CACHE_DIR_ENV_VAR, raising=False)

    configure_tiktoken_cache_env()

    assert TIKTOKEN_CACHE_DIR_ENV_VAR not in os.environ


def test_configure_tiktoken_cache_env_sets_env_var_when_cache_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_estimate, "get_klorb_data_dir", lambda: tmp_path)
    monkeypatch.delenv(TIKTOKEN_CACHE_DIR_ENV_VAR, raising=False)
    install_tiktoken_cache()

    configure_tiktoken_cache_env()

    encoding_dir = tmp_path / "tiktoken-cache" / ENCODING_NAME
    assert os.environ[TIKTOKEN_CACHE_DIR_ENV_VAR] == str(encoding_dir)


def test_configure_tiktoken_cache_env_logs_when_cache_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(token_estimate, "get_klorb_data_dir", lambda: tmp_path)
    monkeypatch.delenv(TIKTOKEN_CACHE_DIR_ENV_VAR, raising=False)
    install_tiktoken_cache()

    with caplog.at_level(logging.INFO, logger="klorb.token_estimate"):
        configure_tiktoken_cache_env()

    assert any(TIKTOKEN_CACHE_DIR_ENV_VAR in record.getMessage() for record in caplog.records)
