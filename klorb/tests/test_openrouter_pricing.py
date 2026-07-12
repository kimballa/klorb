# © Copyright 2026 Aaron Kimball
"""Tests for klorb.models.openrouter_pricing."""

import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from klorb.models.openrouter_pricing import fetch_openrouter_pricing


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
