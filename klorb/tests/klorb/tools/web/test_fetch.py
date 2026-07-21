# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.web.fetch — WebFetchTool."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from klorb.permissions.domain_access import DomainRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.web.fetch import WebFetchTool, _extract_mime_type, _is_text_mime
from klorb.workspace import Workspace


def _context(
    *,
    domain_rules: DomainRules | None = None,
    workspace_root: Path | None = None,
) -> ToolSetupContext:
    """Build a minimal ToolSetupContext for testing WebFetchTool."""
    session_config = SessionConfig()
    if domain_rules is not None:
        session_config.domain_rules = domain_rules
    if workspace_root is not None:
        session_config.workspace = Workspace(path=workspace_root)
    return ToolSetupContext(
        process_config=ProcessConfig(),
        session_config=session_config,
    )


def _make_response(
    content: bytes = b"hello",
    status_code: int = 200,
    reason_phrase: str = "OK",
    content_type: str = "text/plain; charset=utf-8",
    url: str = "https://example.com/",
) -> MagicMock:
    """Create a mock httpx.Response."""
    response = MagicMock()
    response.status_code = status_code
    response.reason_phrase = reason_phrase
    response.headers = {"content-type": content_type}
    response.url = url
    response.encoding = "utf-8"
    response.text = content.decode("utf-8")
    response.iter_bytes.return_value = [content]
    return response


# --- _extract_mime_type ---


def test_extract_mime_type_basic() -> None:
    assert _extract_mime_type("text/html; charset=utf-8") == "text/html"


def test_extract_mime_type_no_params() -> None:
    assert _extract_mime_type("application/json") == "application/json"


def test_extract_mime_type_none() -> None:
    assert _extract_mime_type(None) == "application/octet-stream"


def test_extract_mime_type_empty() -> None:
    assert _extract_mime_type("") == "application/octet-stream"


# --- _is_text_mime ---


def test_is_text_mime_text_prefix() -> None:
    assert _is_text_mime("text/html") is True
    assert _is_text_mime("text/plain") is True


def test_is_text_mime_json() -> None:
    assert _is_text_mime("application/json") is True


def test_is_text_mime_binary() -> None:
    assert _is_text_mime("image/png") is False
    assert _is_text_mime("application/pdf") is False


# --- WebFetchTool basic ---


def test_tool_name_and_category() -> None:
    tool = WebFetchTool(_context())
    assert tool.name() == "WebFetch"
    assert tool.category() == "WEB"
    assert tool.is_read_only() is True


def test_tool_parameters() -> None:
    tool = WebFetchTool(_context())
    params = tool.parameters()
    assert "url" in params.model_fields


def test_non_get_method_returns_error() -> None:
    tool = WebFetchTool(_context())
    result = tool.apply({"url": "https://example.com/", "method": "POST"})
    assert "error" in result
    assert "GET" in result["error"]


def test_invalid_url_returns_error() -> None:
    tool = WebFetchTool(_context())
    result = tool.apply({"url": "not-a-url"})
    assert "error" in result


def test_domain_deny_raises_permission_error() -> None:
    rules = DomainRules(deny=["evil.com"])
    tool = WebFetchTool(_context(domain_rules=rules))
    with pytest.raises(PermissionError, match="denied"):
        tool.apply({"url": "https://evil.com/"})


def test_domain_ask_raises_permission_ask_required() -> None:
    rules = DomainRules(ask=["unknown.com"])
    tool = WebFetchTool(_context(domain_rules=rules))
    with pytest.raises(PermissionAskRequired):
        tool.apply({"url": "https://unknown.com/"})


def test_domain_allow_proceeds() -> None:
    rules = DomainRules(allow=["example.com"])
    tool = WebFetchTool(_context(domain_rules=rules))
    mock_response = _make_response()
    with patch("klorb.tools.web.fetch.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.request.return_value = mock_response
        mock_client_cls.return_value = mock_client
        result = tool.apply({"url": "https://example.com/"})
    assert result["response_code"] == 200
    assert result["untrusted_content"] == "hello"


def test_under_spill_returns_inline() -> None:
    rules = DomainRules(allow=["example.com"])
    tool = WebFetchTool(_context(domain_rules=rules))
    content = b"short content"
    mock_response = _make_response(content=content)
    with patch("klorb.tools.web.fetch.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.request.return_value = mock_response
        mock_client_cls.return_value = mock_client
        result = tool.apply({"url": "https://example.com/"})
    assert result["untrusted_content"] is not None
    assert result["untrusted_content_file"] is None


def test_security_warning_present() -> None:
    rules = DomainRules(allow=["example.com"])
    tool = WebFetchTool(_context(domain_rules=rules))
    mock_response = _make_response()
    with patch("klorb.tools.web.fetch.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.request.return_value = mock_response
        mock_client_cls.return_value = mock_client
        result = tool.apply({"url": "https://example.com/"})
    assert "UNTRUSTED" in result["security_warning"]


def test_redirect_followed() -> None:
    rules = DomainRules(allow=["example.com"])
    tool = WebFetchTool(_context(domain_rules=rules))
    mock_response = _make_response(url="https://example.com/final")
    with patch("klorb.tools.web.fetch.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.request.return_value = mock_response
        mock_client_cls.return_value = mock_client
        result = tool.apply({"url": "https://example.com/old"})
    assert result["url"] == "https://example.com/final"


def test_http_error_returns_error() -> None:
    rules = DomainRules(allow=["example.com"])
    tool = WebFetchTool(_context(domain_rules=rules))
    with patch("klorb.tools.web.fetch.httpx.Client") as mock_client_cls:
        import httpx
        mock_client = MagicMock()
        mock_client.request.side_effect = httpx.ConnectError("Connection refused")
        mock_client_cls.return_value = mock_client
        result = tool.apply({"url": "https://example.com/"})
    assert "error" in result


def test_summary_format() -> None:
    tool = WebFetchTool(_context())
    assert "WebFetch:" in tool.summary({"url": "https://example.com/"})
    assert "failed" in tool.summary({"url": "https://example.com/"}, error="boom")
