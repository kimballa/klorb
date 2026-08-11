# © Copyright 2026 Aaron Kimball
"""Tests for klorb.hooks.classifier_handler.run_classifier_handler: the `type=classifier` hook
handler's structured-output/retry/never-raises contract."""

import json
from datetime import datetime
from unittest.mock import MagicMock

from klorb.api_provider import ProviderResponse
from klorb.hooks.classifier_handler import run_classifier_handler
from klorb.hooks.hook_api import HookInput, HookOutput
from klorb.message import Message


def _reply(content: str) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=1, timestamp=datetime.now(),
            processing_state="complete"),
        prompt_tokens=1)


def _valid_output_json(message: str = "classified") -> str:
    return json.dumps({"message": message})


def _hook_input() -> HookInput:
    return HookInput(hook="onAgentTurnEnd", reason=None, workspace_root="/ws", message="agent said done")


def test_run_classifier_handler_returns_output_on_success() -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply(_valid_output_json("looks done"))

    output = run_classifier_handler(
        "classify whether this is done", _hook_input(), api_provider=provider, model="some/model",
        timeout=5.0, e2e_timeout=10.0)

    assert output == HookOutput(message="looks done")
    provider.send_prompt.assert_called_once()


def test_run_classifier_handler_sends_hook_input_json_then_the_configured_prompt() -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply(_valid_output_json())

    run_classifier_handler(
        "add summary bullet points", _hook_input(), api_provider=provider, model="some/model",
        timeout=5.0, e2e_timeout=10.0)

    args, kwargs = provider.send_prompt.call_args
    sent_prompt = args[0][0].content
    assert '"hook":"onAgentTurnEnd"' in sent_prompt.replace(" ", "")
    assert sent_prompt.endswith("add summary bullet points")
    assert kwargs["model"] == "some/model"
    assert kwargs["timeout"] == 5.0
    assert kwargs["response_format"]["json_schema"]["name"] == "HookOutput"


def test_run_classifier_handler_retries_once_on_malformed_json_then_succeeds() -> None:
    provider = MagicMock()
    provider.send_prompt.side_effect = [_reply("not json at all"), _reply(_valid_output_json("ok"))]

    output = run_classifier_handler(
        "classify", _hook_input(), api_provider=provider, model="some/model", timeout=5.0,
        e2e_timeout=10.0)

    assert output == HookOutput(message="ok")
    assert provider.send_prompt.call_count == 2


def test_run_classifier_handler_gives_up_after_one_retry() -> None:
    provider = MagicMock()
    provider.send_prompt.side_effect = [_reply("not json"), _reply("still not json")]

    output = run_classifier_handler(
        "classify", _hook_input(), api_provider=provider, model="some/model", timeout=5.0,
        e2e_timeout=10.0)

    assert output is None
    assert provider.send_prompt.call_count == 2


def test_run_classifier_handler_returns_none_on_request_failure() -> None:
    provider = MagicMock()
    provider.send_prompt.side_effect = RuntimeError("boom")

    output = run_classifier_handler(
        "classify", _hook_input(), api_provider=provider, model="some/model", timeout=5.0,
        e2e_timeout=10.0)

    assert output is None
