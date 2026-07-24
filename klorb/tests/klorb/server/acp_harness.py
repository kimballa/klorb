# © Copyright 2026 Aaron Kimball
"""`AcpHarness`: wires an `AcpServer` to the ACP SDK's client-side connection over an in-memory
duplex socket pair, so tests exercise real ACP JSON-RPC protocol traffic without a subprocess.
See docs/specs/klorb-server.md's "Testing strategy" section."""

import asyncio
import socket
from typing import Any

import acp

from klorb.api_provider import ApiProvider
from klorb.models.registry import ModelRegistry
from klorb.process_config import ProcessConfig
from klorb.server.acp_server import AcpServer, ServerStreams
from klorb.workspace import TrustManager


class HarnessClient:
    """Implements the ACP `Client` protocol on the test side of the connection, recording every
    `session/update` notification the server sends for assertions.

    Every other `Client` method (`request_permission`, `read_text_file`, terminal handling, ...)
    raises `NotImplementedError`: `KlorbAcpAgent` doesn't call any of them at this checkpoint
    (no tool calls, no permission asks) -- see
    docs/plans/archive/plan-016-001-python-acp-server-core.md. They're still implemented
    (rather than omitted) so this class structurally satisfies `acp.Client`, which
    `acp.connect_to_agent()` requires.
    """

    def __init__(self) -> None:
        self.session_updates: list[acp.SessionNotification] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.session_updates.append(acp.SessionNotification(session_id=session_id, update=update))

    def on_connect(self, conn: Any) -> None:
        pass

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        pass

    async def request_permission(
        self, options: Any, session_id: str, tool_call: Any, **kwargs: Any,
    ) -> Any:
        raise NotImplementedError("HarnessClient.request_permission is not used at this checkpoint")

    async def write_text_file(self, content: str, path: str, session_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError("HarnessClient.write_text_file is not used at this checkpoint")

    async def read_text_file(
        self, path: str, session_id: str, limit: int | None = None, line: int | None = None,
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError("HarnessClient.read_text_file is not used at this checkpoint")

    async def create_terminal(
        self, command: str, session_id: str, args: Any = None, cwd: str | None = None,
        env: Any = None, output_byte_limit: int | None = None, **kwargs: Any,
    ) -> Any:
        raise NotImplementedError("HarnessClient.create_terminal is not used at this checkpoint")

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError("HarnessClient.terminal_output is not used at this checkpoint")

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError("HarnessClient.release_terminal is not used at this checkpoint")

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError("HarnessClient.wait_for_terminal_exit is not used at this checkpoint")

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError("HarnessClient.kill_terminal is not used at this checkpoint")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("HarnessClient.ext_method is not used at this checkpoint")


class AcpHarness:
    """Owns one end of the in-memory duplex pair `build_acp_harness()` sets up: `client` drives
    ACP requests (`initialize`, `new_session`, `prompt`, `cancel`) against the `AcpServer`
    running as a background task; `harness_client` records what came back.
    """

    def __init__(
        self,
        client: Any,
        harness_client: HarnessClient,
        server: AcpServer,
        server_task: "asyncio.Task[int]",
        client_writer: asyncio.StreamWriter,
    ) -> None:
        self.client = client
        self.harness_client = harness_client
        self.server = server
        self.server_task = server_task
        self._client_writer = client_writer

    async def aclose(self) -> int:
        """Close the client's writer -- EOFs the server's `ServerStreams.reader` -- and wait
        for `AcpServer.run()` to finish, returning its exit code."""
        self._client_writer.close()
        return await self.server_task


async def _make_duplex_pair() -> tuple[
    tuple[asyncio.StreamReader, asyncio.StreamWriter],
    tuple[asyncio.StreamReader, asyncio.StreamWriter],
]:
    """Return `((server_reader, server_writer), (client_reader, client_writer))`: two ends of
    an in-memory, full-duplex byte stream built from a `socket.socketpair()`, the same shape
    `ServerStreams.from_stdio()` binds to real process stdio."""
    server_sock, client_sock = socket.socketpair()
    server_reader, server_writer = await asyncio.open_connection(sock=server_sock)
    client_reader, client_writer = await asyncio.open_connection(sock=client_sock)
    return (server_reader, server_writer), (client_reader, client_writer)


async def build_acp_harness(
    process_config: ProcessConfig,
    provider: ApiProvider | None = None,
    model_registry: ModelRegistry | None = None,
    trust_manager: TrustManager | None = None,
) -> AcpHarness:
    """Build an `AcpServer` (running `AcpServer.run()` as a background task) wired to an ACP
    client-side connection over an in-memory duplex socket pair.

    `provider`/`model_registry`/`trust_manager` are forwarded to `AcpServer` -- a test passes a
    scripted `ApiProvider` (see `tests/klorb/session/test_session.py`'s mock pattern) and a
    `TrustManager` pointed at an isolated `projects.json`, so no test touches the real
    `KLORB_DATA_DIR` or makes a real API call.
    """
    (server_reader, server_writer), (client_reader, client_writer) = await _make_duplex_pair()
    streams = ServerStreams(server_reader, server_writer)
    server = AcpServer(
        streams, process_config, provider=provider, model_registry=model_registry,
        trust_manager=trust_manager)
    server_task = asyncio.create_task(server.run())
    harness_client = HarnessClient()
    client = acp.connect_to_agent(harness_client, client_writer, client_reader)
    return AcpHarness(client, harness_client, server, server_task, client_writer)
