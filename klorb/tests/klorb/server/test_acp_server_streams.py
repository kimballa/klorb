# © Copyright 2026 Aaron Kimball
"""Tests for klorb.server.acp_server.ServerStreams -- in particular that `from_stdio()` raises
`asyncio.StreamReader`'s buffer limit well past its 64KiB default, which otherwise crashes the
whole ACP connection (`LimitOverrunError`) the moment a single JSON-RPC line -- e.g. a
`session/prompt` carrying a base64-encoded image attachment -- exceeds it."""

from unittest.mock import AsyncMock, patch

from klorb.server.acp_server import STDIN_STREAM_LIMIT_BYTES, ServerStreams


async def test_from_stdio_passes_the_generous_stream_limit() -> None:
    with patch("klorb.server.acp_server.acp.stdio_streams", new=AsyncMock()) as mock_stdio_streams:
        mock_stdio_streams.return_value = (AsyncMock(), AsyncMock())

        await ServerStreams.from_stdio()

        mock_stdio_streams.assert_called_once_with(limit=STDIN_STREAM_LIMIT_BYTES)
