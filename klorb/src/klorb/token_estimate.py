# © Copyright 2026 Aaron Kimball
"""Rough client-side token-count estimation for a single `Message`, used only to give the
running context-usage footer a live number before a turn's real, server-reported counts come
back (see `klorb.session.Session._settle_estimated_tokens` and
docs/adrs/null-estimated-tokens-when-a-real-count-supersedes-them.md). klorb has no
per-provider tokenizer, so every model is estimated via tiktoken's `o200k_base` encoding
(the vocabulary behind OpenAI's newer models) regardless of which model is actually active --
close enough for a live estimate that a round trip's real count supersedes within moments.

This module also bundles that `o200k_base` encoding's BPE cache as klorb package data, so a
fresh install never has to reach OpenAI's blob storage the first time `estimate_tokens()`
runs. `klorb init` (see `klorb.klorb_init`) copies the packaged cache tree to
`tiktoken_cache_target_dir()`; `klorb.cli.main()` then calls `configure_tiktoken_cache_env()`
at process start to point tiktoken at it, if it's there. See docs/specs/klorb-init.md.
"""

import importlib.resources
import logging
import os
import shutil
from pathlib import Path

import tiktoken

from klorb.paths import KLORB_DATA_DIR

logger = logging.getLogger(__name__)

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


TIKTOKEN_CACHE_RESOURCE_NAME = "tiktoken-cache"
"""Directory name of the packaged tiktoken cache tree within the `klorb.resources` package,
and the subdirectory of `KLORB_DATA_DIR` it's copied to. One subdirectory per tiktoken
encoding name (today, only `ENCODING_NAME`, `"o200k_base"`) holds the sha1-named BPE file
tiktoken's own cache format expects to find directly inside the directory
`$TIKTOKEN_CACHE_DIR` points at (see the `README.cache` file alongside it) — never nested any
deeper, since tiktoken itself doesn't namespace its cache directory by encoding name."""

TIKTOKEN_CACHE_DIR_ENV_VAR = "TIKTOKEN_CACHE_DIR"


def tiktoken_cache_target_dir() -> Path:
    """Where `install_tiktoken_cache()` copies the packaged cache tree to, and where
    `configure_tiktoken_cache_env()` looks for it: `$KLORB_DATA_DIR/tiktoken-cache`."""
    return KLORB_DATA_DIR / TIKTOKEN_CACHE_RESOURCE_NAME


def tiktoken_cache_encoding_dir() -> Path:
    """The directory `configure_tiktoken_cache_env()` points `$TIKTOKEN_CACHE_DIR` at once
    `klorb init` has populated `tiktoken_cache_target_dir()`: the `ENCODING_NAME` subdirectory
    of it, matching the packaged resource tree's own layout."""
    return tiktoken_cache_target_dir() / ENCODING_NAME


def install_tiktoken_cache() -> list[str]:
    """Recursively copy the packaged `tiktoken-cache` resource tree (`klorb.resources/
    tiktoken-cache/`) into `tiktoken_cache_target_dir()`, creating it as needed. Always
    copies — there's no `force` gate like `klorb.klorb_init.write_config_file`/
    `create_symlink` have — since this is package data the running klorb version ships with,
    not something a user hand-edits; re-syncing it on every `klorb init` run keeps it current
    with whatever version is installed, at negligible cost (two small files today).
    `importlib.resources.as_file` materializes the packaged directory to a real filesystem
    path first (handling the case where klorb is installed from a zipped wheel), so
    `shutil.copytree` can walk it directly. Raises `OSError` if the target can't be created or
    written.
    """
    target = tiktoken_cache_target_dir()
    with importlib.resources.as_file(
        importlib.resources.files("klorb.resources").joinpath(TIKTOKEN_CACHE_RESOURCE_NAME)
    ) as source:
        shutil.copytree(source, target, dirs_exist_ok=True)
    return [f"Copied tiktoken cache to {target}."]


def configure_tiktoken_cache_env() -> None:
    """Point tiktoken at the packaged `o200k_base` cache `klorb init` copied to
    `tiktoken_cache_encoding_dir()`, if it's there, by setting the `TIKTOKEN_CACHE_DIR`
    environment variable klorb's own process reads it back through — so this module's first
    `tiktoken.get_encoding(ENCODING_NAME)` call reads the bundled cache file from disk instead
    of downloading it from OpenAI's blob storage. A no-op when that directory doesn't exist
    yet (e.g. a fresh install that hasn't run `klorb init`) — tiktoken falls back to its own
    default cache/download behavior in that case. Called once per process: from
    `klorb.cli.main()` for a one-shot prompt (after logging is configured, so the message
    below is actually visible), or from `klorb.tui.repl.ReplApp.on_mount()` for an interactive
    session (once the Textual app itself is running, so the message routes through the app's
    log / the session log file instead of the `TextualHandler`'s raw-stderr fallback for
    logging that happens before any app is active) — see
    docs/adrs/configure-tiktoken-cache-env-after-repl-app-mounts.md.
    """
    encoding_dir = tiktoken_cache_encoding_dir()
    if not encoding_dir.is_dir():
        return
    os.environ[TIKTOKEN_CACHE_DIR_ENV_VAR] = str(encoding_dir)
    logger.info(
        "Found bundled tiktoken cache at %s; setting %s=%s",
        encoding_dir, TIKTOKEN_CACHE_DIR_ENV_VAR, encoding_dir)
