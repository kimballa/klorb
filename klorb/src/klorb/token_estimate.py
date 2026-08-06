# © Copyright 2026 Aaron Kimball
"""Client-side token-count estimation for a single `Message`, treated as that message's
definitive cost rather than a placeholder for a provider-reported count (see
`klorb.message.Message.num_tokens` and docs/adrs/count-every-message-tokens-client-side-with-
tiktoken.md). klorb has no per-provider tokenizer, so every model is estimated via tiktoken's
`o200k_base` encoding (the vocabulary behind OpenAI's newer models) regardless of which model
is actually active -- close enough for any provider's own tokenizer at negligible cost.

This module also bundles that `o200k_base` encoding's BPE cache as klorb package data, so a
fresh install never has to reach OpenAI's blob storage the first time `estimate_tokens()`
runs. `klorb init` (see `klorb.klorb_init`) copies the packaged cache tree to
`tiktoken_cache_target_dir()`; `klorb.cli.main()` then calls `configure_tiktoken_cache_env()`
at process start to point tiktoken at it, if it's there. See docs/specs/klorb-init.md.
"""

import importlib.resources
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import tiktoken

from klorb.message import Message
from klorb.models.model import Model
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


def tool_token_counts(tool_definitions: list[dict[str, Any]]) -> dict[str, int]:
    """Token count of each tool definition's full function-calling JSON encoding (name +
    description + parameters schema), one entry per `klorb.tools.registry.ToolRegistry.
    tool_definitions()` result -- the fixed per-turn prompt cost of offering that tool,
    independent of which tools a particular turn actually calls.
    """
    return {
        definition["function"]["name"]: estimate_tokens(
            json.dumps(definition, default=str, ensure_ascii=False))
        for definition in tool_definitions
    }


def estimate_image_tokens(width: int, height: int, model: Model) -> int:
    """Estimate an image's token cost for `model`, dispatching on `model.capabilities().get(
    "vision_details", {}).get("token_formula")` -- klorb has no per-provider tokenizer for
    pixels any more than it does for text (see module docstring), so this is a best-effort
    estimate sourced from each vendor's own published billing formula, not a value the
    provider itself reports back.

    `"anthropic_tiles"` is also the fallback for a model with no recognized `token_formula`
    (including `moonshotai/kimi-*`, which has no published formula at all) -- a conservative
    approximation rather than a vendor-verified number for those models.
    """
    vision_details: dict[str, Any] = model.capabilities().get("vision_details") or {}
    formula = vision_details.get("token_formula")
    if formula == "qwen_pixel_ratio":
        effective_formula = formula
        num_tokens = (width * height) // (32 * 32) + 2
    elif formula == "openai_patch_budget":
        effective_formula = formula
        patch_budget: int = vision_details["patch_budget"]
        token_multiplier: float = vision_details["token_multiplier"]
        patches = min(patch_budget, -(-width // 32) * -(-height // 32))
        num_tokens = round(patches * token_multiplier)
    else:
        # Anthropic tiles token formula used as default fallback token estimator.
        effective_formula = "anthropic_tiles"
        num_tokens = (width * height) // 750

    logger.debug("Image msg (%sx%s) estimated at %s tokens using formula '%s'",
            width, height, num_tokens, effective_formula)
    return num_tokens


def estimate_message_tokens(message: Message, model: Model) -> int:
    """Estimate `message`'s total token cost against `model`, the same "definitive cost"
    treatment `Message.num_tokens` gives every other message (see docs/adrs/count-every-
    message-tokens-client-side-with-tiktoken.md) but extended to cover image fragments,
    which `estimate_tokens(message.body())` can't: `body()` JSON-dumps `fragments` verbatim,
    so tiktoken-encoding it would count an image fragment's multi-KB base64 payload as if it
    were prose, producing a number with no relationship to the model's actual multimodal
    billing.

    Each text fragment (or the plain `content`/`streaming_content`, when there are no
    fragments) goes through `estimate_tokens`; each `image_url` fragment goes through
    `estimate_image_tokens`, using its `resized_width`/`resized_height` bookkeeping fields
    (set by `klorb.images.prepare.prepare_image_for_model` before the fragment was built).
    """
    if message.fragments is None:
        if message.streaming_content is not None:
            return estimate_tokens("".join(message.streaming_content))
        return estimate_tokens(message.content)
    total = 0
    for fragment in message.fragments:
        if fragment.type == "image_url":
            assert fragment.resized_width is not None
            assert fragment.resized_height is not None
            total += estimate_image_tokens(fragment.resized_width, fragment.resized_height, model)
        else:
            total += estimate_tokens(fragment.text)
    return total


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
    below is actually visible), or from `klorb.tui.ReplApp.on_mount()` for an interactive
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
