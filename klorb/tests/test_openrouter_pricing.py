# © Copyright 2026 Aaron Kimball
"""Tests for klorb.models.openrouter_pricing."""

import json
import time
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from klorb.models.openrouter_pricing import (
    ModelPricing,
    fetch_openrouter_pricing,
    fetch_openrouter_pricing_for_models,
)


def _response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__.return_value = response
    return response


def test_fetch_openrouter_pricing_converts_per_token_to_per_mtok() -> None:
    payload = {
        "data": [
            {"id": "openai/gpt-5-nano", "pricing": {"prompt": "0.00000005", "completion": "0.0000004"}},
        ],
    }

    with patch("urllib.request.urlopen", return_value=_response(payload)):
        pricing = fetch_openrouter_pricing("openai/gpt-5-nano")

    assert pricing is not None
    assert pricing.input_cost_per_mtok == pytest.approx(0.05)
    assert pricing.output_cost_per_mtok == pytest.approx(0.4)
    assert pricing.currency == "USD"


def test_fetch_openrouter_pricing_returns_none_when_model_not_listed() -> None:
    payload = {
        "data": [
            {"id": "some/other-model", "pricing": {"prompt": "0.0000001", "completion": "0.0000002"}},
        ],
    }

    with patch("urllib.request.urlopen", return_value=_response(payload)):
        pricing = fetch_openrouter_pricing("openai/gpt-5-nano")

    assert pricing is None


def test_fetch_openrouter_pricing_returns_none_on_network_error() -> None:
    with patch("urllib.request.urlopen", side_effect=URLError("no route")):
        pricing = fetch_openrouter_pricing("openai/gpt-5-nano")

    assert pricing is None


def test_fetch_openrouter_pricing_returns_none_on_malformed_json() -> None:
    response = MagicMock()
    response.read.return_value = b"not json"
    response.__enter__.return_value = response

    with patch("urllib.request.urlopen", return_value=response):
        pricing = fetch_openrouter_pricing("openai/gpt-5-nano")

    assert pricing is None


def test_fetch_openrouter_pricing_returns_none_when_pricing_field_missing() -> None:
    payload = {"data": [{"id": "openai/gpt-5-nano"}]}

    with patch("urllib.request.urlopen", return_value=_response(payload)):
        pricing = fetch_openrouter_pricing("openai/gpt-5-nano")

    assert pricing is None


def test_fetch_openrouter_pricing_returns_none_on_malformed_pricing_values() -> None:
    payload = {"data": [{"id": "openai/gpt-5-nano", "pricing": {"prompt": "not-a-number"}}]}

    with patch("urllib.request.urlopen", return_value=_response(payload)):
        pricing = fetch_openrouter_pricing("openai/gpt-5-nano")

    assert pricing is None


def test_fetch_openrouter_pricing_for_models_returns_a_result_per_model() -> None:
    pricing = ModelPricing(input_cost_per_mtok=1.0, output_cost_per_mtok=2.0)
    with patch(
        "klorb.models.openrouter_pricing.fetch_openrouter_pricing",
        side_effect=[pricing, None],
    ) as mock_fetch:
        result = fetch_openrouter_pricing_for_models(["a/one", "b/two"], max_requests_per_second=1000.0)

    assert result == {"a/one": pricing, "b/two": None}
    assert mock_fetch.call_count == 2
    mock_fetch.assert_any_call("a/one", timeout=5.0)
    mock_fetch.assert_any_call("b/two", timeout=5.0)


def test_fetch_openrouter_pricing_for_models_respects_max_requests_per_second() -> None:
    with patch("klorb.models.openrouter_pricing.fetch_openrouter_pricing", return_value=None):
        start = time.monotonic()
        fetch_openrouter_pricing_for_models(["a", "b", "c"], max_requests_per_second=20.0)
        elapsed = time.monotonic() - start

    # Three requests at 20/s enforce two inter-request waits of 0.05s each.
    assert elapsed >= 0.08
