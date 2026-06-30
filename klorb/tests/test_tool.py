# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.tool."""

import pytest

from klorb.tools.tool import Tool


def test_tool_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Tool()  # type: ignore[abstract]
