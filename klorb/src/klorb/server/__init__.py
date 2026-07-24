# © Copyright 2026 Aaron Kimball
"""The `klorb.server` package: a persistent klorb process that speaks the Agent Client Protocol
(ACP) over newline-delimited JSON-RPC on stdin/stdout, for driving klorb from another program
(an IDE extension, a supervisor process, a test harness) rather than a terminal. See
docs/specs/klorb-server.md.

This top-level module re-exports `AcpServer`/`ServerStreams` (`klorb.server.acp_server`) and
`KlorbAcpAgent` (`klorb.server.klorb_agent`) for callers outside this package (`klorb.cli`) to
import directly.
"""

from klorb.server.acp_server import AcpServer, ServerStreams
from klorb.server.klorb_agent import KlorbAcpAgent

__all__ = [
    "AcpServer",
    "KlorbAcpAgent",
    "ServerStreams",
]
