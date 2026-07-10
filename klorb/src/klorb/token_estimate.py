# © Copyright 2026 Aaron Kimball
"""Rough client-side token-count estimation for a single `Message`, used only to give the
running context-usage footer a live number before a turn's real, server-reported counts come
back (see `klorb.session.Session._settle_estimated_tokens` and
docs/adrs/null-estimated-tokens-when-a-real-count-supersedes-them.md). klorb has no
per-provider tokenizer, so every model is estimated via tiktoken's `o200k_base` encoding
(the vocabulary behind OpenAI's newer models) regardless of which model is actually active --
close enough for a live estimate that a round trip's real count supersedes within moments."""

import tiktoken

ENCODING_NAME = "o200k_base"

_encoding: tiktoken.Encoding | None = None


def _encoding_instance() -> tiktoken.Encoding:
    """Return the shared `tiktoken.Encoding`, loading it (and caching the result) on first
    use rather than at import time, so a klorb invocation that never estimates a token pays
    no cost for it."""
    global _encoding
    if _encoding is None:
        _encoding = tiktoken.get_encoding(ENCODING_NAME)
    return _encoding


def estimate_tokens(text: str) -> int:
    """Estimate `text`'s token count via tiktoken's `ENCODING_NAME` encoding.
    `disallowed_special=()` treats any substring that looks like a special token (e.g. a user
    pasting literal `"<|endoftext|>"`) as ordinary text instead of raising -- message content
    is never actual API special-token markup, so no substring here should ever be rejected."""
    if not text:
        return 0
    return len(_encoding_instance().encode(text, disallowed_special=()))
