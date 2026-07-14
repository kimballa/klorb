# © Copyright 2026 Aaron Kimball
"""Live per-model pricing lookups against OpenRouter's public models listing.

Unlike the rest of a model's data (capabilities, family/version, ...), published cost per
token can change at any time -- providers reprice, promotional rates expire -- so it is
deliberately never baked into a model's `klorb-model` JSON file (see
docs/adrs/fetch-model-pricing-live-not-from-json.md). `fetch_openrouter_pricing()` asks
OpenRouter for the current number instead, each time a caller (today, only "Show model
info" — see `klorb.tui.model_info_commands`) wants to display it.
"""

import json
import logging
import time
import urllib.request
from collections.abc import Iterable

from pydantic import BaseModel

from klorb.openrouter import OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_ENDPOINT = f"{OPENROUTER_BASE_URL}/models"
DEFAULT_PRICING_FETCH_TIMEOUT = 5.0
"""Default socket timeout (seconds) for `fetch_openrouter_pricing()` — short enough that a
"Show model info" command doesn't hang the UI for long if OpenRouter is unreachable."""

MAX_PRICING_REQUESTS_PER_SECOND = 8.0
"""Ceiling on how many `fetch_openrouter_pricing()` calls `fetch_openrouter_pricing_for_models()`
issues per second, so looking up pricing for a long model list (e.g. `klorb models --costs`)
doesn't hammer OpenRouter with a burst of near-simultaneous requests. Edit by hand if
OpenRouter's actual rate limit ever changes."""


class ModelPricing(BaseModel):
    """A model's cost per million tokens ("MTok") sent/received, as reported live by
    OpenRouter's models listing at the moment it was fetched — not a stored, potentially
    stale fact about the model."""

    input_cost_per_mtok: float
    output_cost_per_mtok: float
    currency: str = "USD"


def fetch_openrouter_pricing(
    model_name: str, *, timeout: float = DEFAULT_PRICING_FETCH_TIMEOUT,
) -> ModelPricing | None:
    """Look up `model_name`'s current per-token cost from OpenRouter's public model listing
    (`GET /models`, no API key required), converting OpenRouter's dollars-per-token pricing
    into dollars-per-million-tokens.

    Returns `None` — never raises — if the request fails, times out, the response doesn't
    parse, or `model_name` isn't listed: this is best-effort live data for a UI display, not
    something any turn depends on. Blocking (uses `urllib.request` rather than an async HTTP
    client); callers on the Textual event loop should run it off-thread (e.g.
    `asyncio.to_thread`) rather than call it directly.
    """
    try:
        with urllib.request.urlopen(OPENROUTER_MODELS_ENDPOINT, timeout=timeout) as response:
            payload = json.loads(response.read())
    except (OSError, ValueError) as exc:
        logger.warning("Failed to fetch OpenRouter model pricing for %s: %s", model_name, exc)
        return None

    for entry in payload.get("data", []):
        if entry.get("id") != model_name:
            continue
        pricing = entry.get("pricing")
        if not isinstance(pricing, dict):
            return None
        try:
            return ModelPricing(
                input_cost_per_mtok=float(pricing["prompt"]) * 1_000_000,
                output_cost_per_mtok=float(pricing["completion"]) * 1_000_000)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Malformed OpenRouter pricing for %s: %s", model_name, exc)
            return None
    return None


def fetch_openrouter_pricing_for_models(
    model_names: Iterable[str],
    *,
    timeout: float = DEFAULT_PRICING_FETCH_TIMEOUT,
    max_requests_per_second: float = MAX_PRICING_REQUESTS_PER_SECOND,
) -> dict[str, ModelPricing | None]:
    """Look up pricing for every name in `model_names`, one `fetch_openrouter_pricing()` call
    per model, throttled to at most `max_requests_per_second` requests per second (see
    `MAX_PRICING_REQUESTS_PER_SECOND`). Returns a dict keyed by model name; a name whose lookup
    failed (network error, unlisted model, malformed response — see `fetch_openrouter_pricing`)
    maps to `None`.
    """
    min_interval = 1.0 / max_requests_per_second
    results: dict[str, ModelPricing | None] = {}
    last_request_at: float | None = None
    for model_name in model_names:
        if last_request_at is not None:
            wait = min_interval - (time.monotonic() - last_request_at)
            if wait > 0:
                time.sleep(wait)
        last_request_at = time.monotonic()
        results[model_name] = fetch_openrouter_pricing(model_name, timeout=timeout)
    return results
