# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.response_envelope."""

from fixtures.sample_tools.echo_tool import EchoTool

from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.exceptions import NoSuchToolException, ToolCallError
from klorb.tools.response_envelope import (
    SystemInterjectionPayload,
    ToolCallErrorInfo,
    ToolResponseEnvelope,
    classify_exception,
    wrap_system_interjection,
)
from klorb.tools.setup_context import ToolSetupContext


def _echo_tool() -> EchoTool:
    return EchoTool(ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig()))


def test_success_is_never_retryable() -> None:
    envelope = ToolResponseEnvelope.success({"matches": []})
    assert envelope.is_error is False
    assert envelope.is_retryable is False
    assert envelope.error_category is None
    assert envelope.error_message is None
    assert envelope.response_body == {"matches": []}


def test_error_retryable_categories() -> None:
    for category in ("transient", "syntax", "validation"):
        envelope = ToolResponseEnvelope.error("boom", category=category)  # type: ignore[arg-type]
        assert envelope.is_error is True
        assert envelope.is_retryable is True


def test_error_non_retryable_categories() -> None:
    for category in ("permission", "business_logic"):
        envelope = ToolResponseEnvelope.error("boom", category=category)  # type: ignore[arg-type]
        assert envelope.is_retryable is False


def test_error_with_no_category_is_not_retryable() -> None:
    envelope = ToolResponseEnvelope.error("boom", category=None)
    assert envelope.is_retryable is False
    assert envelope.error_category is None


def test_error_message_may_be_none_when_response_body_carries_the_detail() -> None:
    envelope = ToolResponseEnvelope.error(
        None, category="business_logic", response_body={"failure_reason": "exit 1"})
    assert envelope.error_message is None
    assert envelope.response_body == {"failure_reason": "exit 1"}


def test_to_wire_dict_omits_empty_system_interjections() -> None:
    envelope = ToolResponseEnvelope.success("ok")
    wire = envelope.to_wire_dict()
    assert "system_interjections" not in wire
    assert wire["response_body"] == "ok"


def test_to_wire_dict_includes_nonempty_system_interjections() -> None:
    envelope = ToolResponseEnvelope.success(
        "ok", system_interjections=(SystemInterjectionPayload(subject="foo", body="bar"),))
    wire = envelope.to_wire_dict()
    assert wire["system_interjections"] == [{"subject": "foo", "body": "bar"}]


def test_to_wire_dict_excludes_none_fields() -> None:
    envelope = ToolResponseEnvelope.success("ok")
    wire = envelope.to_wire_dict()
    assert "error_category" not in wire
    assert "error_message" not in wire


def test_classify_exception_tool_call_error() -> None:
    exc = ToolCallError("nope", category="transient", response_body={"partial": True})
    message, category, response_body = classify_exception(exc)
    assert message == "nope"
    assert category == "transient"
    assert response_body == {"partial": True}


def test_classify_exception_permission_error() -> None:
    message, category, response_body = classify_exception(PermissionError("denied"))
    assert message == "denied"
    assert category == "permission"
    assert response_body is None


def test_classify_exception_value_error() -> None:
    message, category, response_body = classify_exception(ValueError("bad arg"))
    assert message == "bad arg"
    assert category == "validation"
    assert response_body is None


def test_classify_exception_no_such_tool_exception() -> None:
    message, category, response_body = classify_exception(NoSuchToolException("Frobnicate"))
    assert category == "validation"
    assert response_body is None
    assert "Frobnicate" in message


def test_classify_exception_unclassified() -> None:
    message, category, response_body = classify_exception(RuntimeError("mystery"))
    assert message == "mystery"
    assert category is None
    assert response_body is None


def test_error_info_carries_only_the_non_body_fields() -> None:
    envelope = ToolResponseEnvelope.error(
        "boom", category="validation", response_body={"detail": "x"})

    assert envelope.error_info() == ToolCallErrorInfo(
        is_error=True, is_retryable=True, error_category="validation", error_message="boom")


def test_error_info_on_success() -> None:
    envelope = ToolResponseEnvelope.success({"matches": []})

    assert envelope.error_info() == ToolCallErrorInfo(
        is_error=False, is_retryable=False, error_category=None, error_message=None)


def test_wrap_system_interjection_shape() -> None:
    assert wrap_system_interjection("TodoNext", "do the thing") == (
        '<SystemInterjection subject="TodoNext">\ndo the thing\n</SystemInterjection>')


def test_to_wire_content_success_renders_only_the_body() -> None:
    envelope = ToolResponseEnvelope.success({"matches": ["a.py"]})

    content = envelope.to_wire_content(_echo_tool())

    assert content == '{"matches": ["a.py"]}'


def test_to_wire_content_success_with_no_body_is_empty() -> None:
    envelope = ToolResponseEnvelope.success(None)

    assert envelope.to_wire_content(_echo_tool()) == ""


def test_to_wire_content_uses_json_dump_when_tool_is_none() -> None:
    envelope = ToolResponseEnvelope.success({"matches": ["a.py"]})

    assert envelope.to_wire_content(None) == '{"matches": ["a.py"]}'


def test_to_wire_content_error_omits_body_when_none() -> None:
    envelope = ToolResponseEnvelope.error("boom", category="validation")

    content = envelope.to_wire_content(_echo_tool())

    assert content == "is_error: true\nerror_category: validation\nis_retryable: true\nerror_message: boom"


def test_to_wire_content_error_omits_category_and_message_when_unset() -> None:
    envelope = ToolResponseEnvelope.error(None, category=None)

    content = envelope.to_wire_content(_echo_tool())

    assert content == "is_error: true\nis_retryable: false"


def test_to_wire_content_error_includes_formatted_body_after_headers() -> None:
    envelope = ToolResponseEnvelope.error(
        None, category="business_logic", response_body={"stdout": "boom"})

    content = envelope.to_wire_content(_echo_tool())

    assert content == (
        "is_error: true\nerror_category: business_logic\nis_retryable: false"
        '\n\n{"stdout": "boom"}')


def test_to_wire_content_success_never_shows_is_error_line() -> None:
    envelope = ToolResponseEnvelope.success({"ok": True})

    content = envelope.to_wire_content(_echo_tool())

    assert "is_error" not in content
    assert "is_retryable" not in content


def test_to_wire_content_prepends_interjections_as_xml() -> None:
    envelope = ToolResponseEnvelope.success(
        {"ok": True}, system_interjections=(SystemInterjectionPayload(subject="foo", body="bar"),))

    content = envelope.to_wire_content(_echo_tool())

    assert content == '<SystemInterjection subject="foo">\nbar\n</SystemInterjection>\n\n{"ok": true}'


def test_to_wire_content_joins_multiple_interjections_with_blank_line() -> None:
    envelope = ToolResponseEnvelope.success(None, system_interjections=(
        SystemInterjectionPayload(subject="foo", body="bar"),
        SystemInterjectionPayload(subject="baz", body="qux"),
    ))

    content = envelope.to_wire_content(_echo_tool())

    assert content == (
        '<SystemInterjection subject="foo">\nbar\n</SystemInterjection>\n\n'
        '<SystemInterjection subject="baz">\nqux\n</SystemInterjection>')
