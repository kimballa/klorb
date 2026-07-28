# © Copyright 2026 Aaron Kimball
"""`AcpHarness`: wires an `AcpServer` to the ACP SDK's client-side connection over an in-memory
duplex socket pair, so tests exercise real ACP JSON-RPC protocol traffic without a subprocess.
See docs/specs/klorb-server.md's "Testing strategy" section."""

import asyncio
import socket
from collections.abc import Callable
from typing import Any

import acp

from klorb.api_provider import ApiProvider
from klorb.models.registry import ModelRegistry
from klorb.process_config import ProcessConfig
from klorb.server.acp_server import AcpServer, ServerStreams
from klorb.workspace import TrustManager


class RecordedPermissionRequest:
    """One `request_permission()` call `HarnessClient` received, recorded verbatim for a test's
    assertions: `meta` is the `_meta.klorb` payload unpacked back into kwargs by the SDK's own
    router (see `acp.router.MessageRouter._make_func`), not the raw `_meta` dict."""

    def __init__(
        self, options: list[acp.schema.PermissionOption], session_id: str,
        tool_call: acp.schema.ToolCallUpdate, meta: dict[str, Any],
    ) -> None:
        self.options = options
        self.session_id = session_id
        self.tool_call = tool_call
        self.meta = meta


class HarnessClient:
    """Implements the ACP `Client` protocol on the test side of the connection, recording every
    `session/update` notification the server sends for assertions.

    `request_permission()`/`ext_method()` record every call into `permission_requests`/
    `ext_method_calls` and answer via `on_request_permission`/`on_ext_method` -- settable by a
    test before driving a turn -- defaulting to `None`, which still raises `NotImplementedError`
    (a test exercising a path that shouldn't ask must fail loudly, not hang). `ext_notification()`
    (fire-and-forget, e.g. `_klorb/usage`) just records every call into `ext_notification_calls`
    -- there's nothing to answer. Every other `Client` method (`read_text_file`, terminal
    handling, ...) still raises `NotImplementedError` unconditionally: `KlorbAcpAgent` doesn't
    call any of them yet. They're still implemented (rather than omitted) so this class
    structurally satisfies `acp.Client`, which `acp.connect_to_agent()` requires.
    """

    def __init__(self) -> None:
        self.session_updates: list[acp.SessionNotification] = []
        self.permission_requests: list[RecordedPermissionRequest] = []
        self.on_request_permission: (
            Callable[[RecordedPermissionRequest], acp.RequestPermissionResponse] | None
        ) = None
        self.ext_method_calls: list[tuple[str, dict[str, Any]]] = []
        self.on_ext_method: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
        self.ext_notification_calls: list[tuple[str, dict[str, Any]]] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.session_updates.append(acp.SessionNotification(session_id=session_id, update=update))

    def on_connect(self, conn: Any) -> None:
        pass

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        self.ext_notification_calls.append((method, params))

    async def request_permission(
        self, options: Any, session_id: str, tool_call: Any, **kwargs: Any,
    ) -> Any:
        recorded = RecordedPermissionRequest(options, session_id, tool_call, kwargs)
        self.permission_requests.append(recorded)
        if self.on_request_permission is None:
            raise NotImplementedError("HarnessClient.on_request_permission was not set by the test")
        return self.on_request_permission(recorded)

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
        self.ext_method_calls.append((method, params))
        if self.on_ext_method is None:
            raise NotImplementedError("HarnessClient.on_ext_method was not set by the test")
        return self.on_ext_method(method, params)


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
    # `use_unstable_protocol=True`: matches `AcpServer.run()`'s own flag (see its docstring) --
    # `session/list` is still marked unstable in the ACP SDK's router, gated independently on
    # each side of the connection.
    client = acp.connect_to_agent(
        harness_client, client_writer, client_reader, use_unstable_protocol=True)
    return AcpHarness(client, harness_client, server, server_task, client_writer)
