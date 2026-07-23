# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.response_envelope."""

from klorb.tools.exceptions import NoSuchToolException, ToolCallError
from klorb.tools.response_envelope import SystemInterjectionPayload, ToolResponseEnvelope, classify_exception


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


def test_to_wire_dict_omits_empty_interjection_lists() -> None:
    envelope = ToolResponseEnvelope.success("ok")
    wire = envelope.to_wire_dict()
    assert "system_interjections" not in wire
    assert "user_interjections" not in wire
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
