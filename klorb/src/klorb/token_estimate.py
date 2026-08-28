# © Copyright 2026 Aaron Kimball
"""Client-side token-count estimation for a single `Message`, treated as that message's
definitive cost rather than a placeholder for a provider-reported count.
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
from klorb.paths import get_klorb_data_dir

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
    `disallowed_special=()` treats any substring that looks like a special token as ordinary
    text instead of raising."""
    if not text:
        return 0
    return len(_encoding_instance().encode(text, disallowed_special=()))


def tool_token_counts(tool_definitions: list[dict[str, Any]]) -> dict[str, int]:
    """Token count of each tool definition's full JSON encoding. A `"local"`-mode tool is keyed
    by its function name (`definition["function"]["name"]`); a `"server"`-mode tool (see
    `klorb.tools.server_tool.ServerTool`) has no such wrapper, so it's keyed by its own
    `definition["type"]` instead (e.g. `"openrouter:web_search"`).
    """
    return {
        (definition["function"]["name"] if "function" in definition else definition["type"]):
            estimate_tokens(json.dumps(definition, default=str, ensure_ascii=False))
        for definition in tool_definitions
    }


def estimate_image_tokens(width: int, height: int, model: Model) -> int:
    """Estimate an image's token cost for `model`, dispatching on its `token_formula`
    capability. This is a best-effort estimate sourced from each vendor's published billing
    formula, not a value the provider reports back.

    `"anthropic_tiles"` is the fallback for a model with no recognized `token_formula`.
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
    """Estimate `message`'s total token cost against `model`. Extended beyond plain
    `estimate_tokens(message.body())` to cover image fragments, which `body()` can't
    handle properly -- tiktoken-encoding a base64 payload produces a number unrelated to
    the model's actual multimodal billing.

    Each text fragment goes through `estimate_tokens`; each `image_url` fragment goes through
    `estimate_image_tokens`.
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
and the subdirectory of `KLORB_DATA_DIR` it's copied to."""

TIKTOKEN_CACHE_DIR_ENV_VAR = "TIKTOKEN_CACHE_DIR"


def tiktoken_cache_target_dir() -> Path:
    """Where `install_tiktoken_cache()` copies the packaged cache tree to, and where
    `configure_tiktoken_cache_env()` looks for it: `$KLORB_DATA_DIR/tiktoken-cache`."""
    return get_klorb_data_dir() / TIKTOKEN_CACHE_RESOURCE_NAME


def tiktoken_cache_encoding_dir() -> Path:
    """The directory `configure_tiktoken_cache_env()` points `$TIKTOKEN_CACHE_DIR` at once
    `klorb init` has populated `tiktoken_cache_target_dir()`: the `ENCODING_NAME` subdirectory
    of it, matching the packaged resource tree's own layout."""
    return tiktoken_cache_target_dir() / ENCODING_NAME


def install_tiktoken_cache() -> list[str]:
    """Recursively copy the packaged `tiktoken-cache` resource tree into
    `tiktoken_cache_target_dir()`, creating it as needed. Always copies since this is
    package data the running klorb version ships with. Raises `OSError` if the target can't
    be created or written.
    """
    target = tiktoken_cache_target_dir()
    with importlib.resources.as_file(
        importlib.resources.files("klorb.resources").joinpath(TIKTOKEN_CACHE_RESOURCE_NAME)
    ) as source:
        shutil.copytree(source, target, dirs_exist_ok=True)
    return [f"Copied tiktoken cache to {target}."]


def configure_tiktoken_cache_env() -> None:
    """Point tiktoken at the packaged `o200k_base` cache by setting the
    `TIKTOKEN_CACHE_DIR` environment variable, if the cache directory exists. A no-op when
    that directory doesn't exist yet.
    """
    encoding_dir = tiktoken_cache_encoding_dir()
    if not encoding_dir.is_dir():
        return
    os.environ[TIKTOKEN_CACHE_DIR_ENV_VAR] = str(encoding_dir)
    logger.info(
        "Found bundled tiktoken cache at %s; setting %s=%s",
        encoding_dir, TIKTOKEN_CACHE_DIR_ENV_VAR, encoding_dir)
