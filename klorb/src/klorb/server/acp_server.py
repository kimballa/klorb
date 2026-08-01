# © Copyright 2026 Aaron Kimball
"""`ServerStreams`/`AcpServer`: the Agent Client Protocol server behind `klorb server` -- see
`klorb.cli.run_server_cli` and docs/specs/klorb-server.md for the wire protocol."""

import asyncio
import logging
from pathlib import Path

import acp

from klorb.api_provider import ApiProvider
from klorb.models.registry import ModelRegistry
from klorb.process_config import ProcessConfig
from klorb.server.klorb_agent import KlorbAcpAgent
from klorb.workspace import TrustManager

logger = logging.getLogger(__name__)

STDIN_STREAM_LIMIT_BYTES = 64 * 1024 * 1024
"""Buffer limit for the stdin `asyncio.StreamReader` `ServerStreams.from_stdio()` builds, well
above `asyncio.StreamReader`'s own 64KiB default. ACP frames one JSON-RPC message per line, and
an image `session/prompt` request's `data` field is the *raw* attachment (klorb resizes/
transcodes server-side -- see docs/specs/vision-image-input.md), base64-inflated (~4/3) and
client-side-capped at 25MB per image (`PromptInput.MAX_ATTACHMENT_RAW_BYTES`) with no cap on
attachment count per turn -- comfortably over the default limit, which fails closed as an
unrecoverable `LimitOverrunError` that tears down the whole connection, not a per-request error.
"""


class ServerStreams:
    """Owns the async reader/writer pair an ACP connection is built from.

    `from_stdio()` is the only place real process stdio is bound; every other construction
    (tests, a future websocket transport) injects its own reader/writer pair directly, via the
    constructor.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    @property
    def reader(self) -> asyncio.StreamReader:
        return self._reader

    @property
    def writer(self) -> asyncio.StreamWriter:
        return self._writer

    @classmethod
    async def from_stdio(cls) -> "ServerStreams":
        """Bind this process's real `stdin`/`stdout` as an ACP stream pair, via the SDK's own
        `acp.stdio_streams()` helper, with `STDIN_STREAM_LIMIT_BYTES` as the stdin reader's
        buffer limit (see its own docstring)."""
        reader, writer = await acp.stdio_streams(limit=STDIN_STREAM_LIMIT_BYTES)
        logger.debug(
            "Bound ACP server streams to process stdio (stdin limit=%d bytes)",
            STDIN_STREAM_LIMIT_BYTES)
        return cls(reader, writer)


class AcpServer:
    """Runs a `KlorbAcpAgent` over `streams` until the client disconnects, then returns.

    Constructed with a `ServerStreams` and a `ProcessConfig` -- the template every ACP
    `session/new` request's `SessionConfig` is copied from (see `KlorbAcpAgent.new_session`).
    `provider`/`model_registry`/`trust_manager`/`config_flag_path` are forwarded to the
    `KlorbAcpAgent` this constructs, exactly like `KlorbAcpAgent`'s own optional constructor
    params -- `None` (the default) means a real one; a test harness injects a scripted
    `ApiProvider` and an isolated `TrustManager` here instead. The `KlorbAcpAgent` is built
    once, here, and exposed via `agent` so a test harness can inspect its live `Session`
    directly.
    """

    def __init__(
        self,
        streams: ServerStreams,
        process_config: ProcessConfig,
        provider: ApiProvider | None = None,
        model_registry: ModelRegistry | None = None,
        trust_manager: TrustManager | None = None,
        config_flag_path: Path | None = None,
    ) -> None:
        self._streams = streams
        self._agent = KlorbAcpAgent(
            process_config, provider=provider, model_registry=model_registry, trust_manager=trust_manager,
            config_flag_path=config_flag_path)

    @property
    def agent(self) -> KlorbAcpAgent:
        """The `KlorbAcpAgent` this server runs -- exposed read-only so a test harness can
        inspect its live `Session` (`agent.session`) without a separate injection seam."""
        return self._agent

    async def run(self) -> int:
        """Serve ACP requests until the client disconnects (EOF on `streams.reader`), close any
        live session, and return `0`. There is no error condition here that produces a non-zero
        return -- a malformed or unrecognized request becomes a JSON-RPC error reply, handled by
        the SDK's own connection machinery, not a process failure.

        `use_unstable_protocol=True`: `session/list` (`KlorbAcpAgent.list_sessions`) is still
        marked unstable in the ACP spec/SDK (unlike `session/load`, which is stable) -- the
        SDK's own request router rejects it with `method_not_found` outright unless this flag is
        set, regardless of whether the agent implements the method. `session/load` and every
        other implemented method are unaffected either way.
        """
        logger.debug("klorb ACP server starting")
        try:
            await acp.run_agent(
                self._agent, input_stream=self._streams.writer, output_stream=self._streams.reader,
                use_unstable_protocol=True)
        finally:
            self._agent.close()
        logger.debug("klorb ACP server stopping")
        return 0
