# © Copyright 2026 Aaron Kimball
"""Tests for klorb.token_estimate."""

import tiktoken

from klorb.token_estimate import ENCODING_NAME, estimate_tokens


def test_estimate_tokens_empty_string_is_zero() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_matches_the_underlying_encoding() -> None:
    text = "hello there, this is a test message with several words in it."
    encoding = tiktoken.get_encoding(ENCODING_NAME)
    assert estimate_tokens(text) == len(encoding.encode(text))


def test_estimate_tokens_does_not_raise_on_special_token_like_substrings() -> None:
    assert estimate_tokens("<|endoftext|>") > 0
