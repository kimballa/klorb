# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.tool."""

import pytest
from fixtures.sample_tools.echo_tool import EchoTool

from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool


def test_tool_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Tool()  # type: ignore[abstract, call-arg]


def test_subclass_constructor_takes_only_a_tool_setup_context() -> None:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())

    tool = EchoTool(context)

    assert tool.context is context
