# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.server_tool — ServerTool."""

from typing import Any

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.server_tool import ServerTool
from klorb.tools.setup_context import ToolSetupContext


class _SampleServerTool(ServerTool):
    def name(self) -> str:
        return "sample_server_tool"

    def description(self) -> str:
        return "A sample server tool, used only in tests."

    def category(self) -> str:
        return "WEB"

    def is_read_only(self) -> bool:
        return True

    def provider_definition(self) -> dict[str, Any]:
        return {"type": "some:provider_tool", "parameters": {}}


def _tool() -> _SampleServerTool:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())
    return _SampleServerTool(context)


def test_execution_mode_is_server() -> None:
    assert _tool().execution_mode() == "server"


def test_parameters_unused() -> None:
    assert _tool().parameters() == {}


def test_apply_raises_runtime_error() -> None:
    with pytest.raises(RuntimeError, match="ServerTool"):
        _tool().apply({})
