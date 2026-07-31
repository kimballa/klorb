# © Copyright 2026 Aaron Kimball
"""Tests for klorb.sandbox.network: `ProxyBackend`'s SOCKS5/HTTP CONNECT protocol handling,
domain evaluation against `bash_domain_rules`, `BlockedDomainRecorder` bookkeeping, and the
relay-stub/bootstrap-script generation. See docs/specs/bash-tool-and-command-permissions.md's
"Sandboxing" section.

`ProxyBackend` itself never cares whether an accepted connection's fd came from a real TCP
listener or anything else -- it just wraps it as a `socket.socket` and speaks the protocol over
it. So these tests hand it one end of an ordinary `socket.socketpair()` in place of the in-sandbox
relay stub's real TCP `accept()`, exactly the way `socket.send_fds`/`recv_fds` pass a real
accepted connection's fd across the control channel -- no real TCP port, and no real `bwrap`
sandbox, is needed to exercise `ProxyBackend` in isolation. End-to-end coverage through a real
`bwrap` sandbox and the actual relay stub lives in `tests/klorb/tools/test_bash.py`'s
`requires_bwrap` section.
"""

import socket
import threading
import time
from collections.abc import Iterator
from typing import Callable

import pytest

from klorb.permissions.domain_access import DomainRules
from klorb.sandbox.network import (
    _RELAY_HEREDOC_DELIM,
    _RELAY_STUB_SOURCE,
    BlockedDomainRecorder,
    ProxyBackend,
    _bootstrap_script,
    proxy_env_vars,
)
from klorb.session import SessionConfig

_TIMEOUT = 5.0


class _Harness:
    """One live `ProxyBackend` wired to a fake in-sandbox relay: `open_client(tag)` hands back a
    connected client socket as if the relay had just accepted it and passed its fd across."""

    def __init__(self, session_config: SessionConfig) -> None:
        self.recorder = BlockedDomainRecorder()
        self._host_sock, self._sandbox_sock = socket.socketpair()
        self.backend = ProxyBackend(self._host_sock, session_config, self.recorder.record)
        self.backend.start()

    def open_client(self, tag: bytes) -> socket.socket:
        client, accepted = socket.socketpair()
        client.settimeout(_TIMEOUT)
        socket.send_fds(self._sandbox_sock, [tag], [accepted.fileno()])
        accepted.close()
        return client

    def close(self) -> None:
        self._sandbox_sock.close()
        self.backend.close()


@pytest.fixture
def echo_server() -> Iterator[Callable[[], int]]:
    """Starts (lazily, once) a local TCP echo server, returning a callable giving its port --
    used as the real outbound target an `"allow"`-verdict `CONNECT` should actually reach."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            srv.settimeout(0.2)
            try:
                conn, _addr = srv.accept()
            except OSError:
                continue
            data = conn.recv(4096)
            conn.sendall(data.upper())
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield lambda: srv.getsockname()[1]
    stop.set()
    srv.close()


def _harness(*, allow: list[str] | None = None, deny: list[str] | None = None) -> _Harness:
    session_config = SessionConfig()
    session_config.bash_domain_rules = DomainRules(deny=deny or [], allow=allow or [])
    return _Harness(session_config)


# --- SOCKS5 ---


def test_socks5_allowed_connect_relays_bytes(echo_server: Callable[[], int]) -> None:
    harness = _harness(allow=["127.0.0.1"])
    try:
        client = harness.open_client(b"S")
        client.sendall(bytes([0x05, 0x01, 0x00]))
        assert client.recv(2) == bytes([0x05, 0x00])

        port = echo_server()
        request = bytes([0x05, 0x01, 0x00, 0x01]) + socket.inet_aton("127.0.0.1") + port.to_bytes(2, "big")
        client.sendall(request)
        reply = client.recv(10)
        assert reply[1] == 0x00

        client.sendall(b"hello")
        assert client.recv(64) == b"HELLO"
        client.close()
    finally:
        harness.close()


def test_socks5_denied_domain_refused_and_recorded() -> None:
    harness = _harness(deny=["denied.example"])
    try:
        client = harness.open_client(b"S")
        client.sendall(bytes([0x05, 0x01, 0x00]))
        assert client.recv(2) == bytes([0x05, 0x00])

        target = b"denied.example"
        request = (
            bytes([0x05, 0x01, 0x00, 0x03, len(target)]) + target + (443).to_bytes(2, "big"))
        client.sendall(request)
        reply = client.recv(10)
        assert reply[1] == 0x02  # connection not allowed by ruleset.
        client.close()
    finally:
        time.sleep(0.1)
        assert harness.recorder.domains == ["denied.example"]
        harness.close()


def test_socks5_unresolved_ask_also_refuses_immediately_no_hang() -> None:
    """A domain matching no rule at all normalizes to "ask" -- v1 fails it closed exactly like an
    explicit "deny", rather than hanging waiting for a live confirmation that doesn't exist yet."""
    harness = _harness()  # empty bash_domain_rules -- nothing allowed, nothing denied.
    try:
        client = harness.open_client(b"S")
        client.sendall(bytes([0x05, 0x01, 0x00]))
        assert client.recv(2) == bytes([0x05, 0x00])
        target = b"unknown.example"
        request = (
            bytes([0x05, 0x01, 0x00, 0x03, len(target)]) + target + (443).to_bytes(2, "big"))
        start = time.monotonic()
        client.sendall(request)
        reply = client.recv(10)
        elapsed = time.monotonic() - start
        assert reply[1] == 0x02
        assert elapsed < 1.0
        client.close()
    finally:
        harness.close()


def test_socks5_no_acceptable_auth_method_rejected() -> None:
    harness = _harness(allow=["example.com"])
    try:
        client = harness.open_client(b"S")
        # Offer only username/password auth (0x02) -- no-auth (0x00) is the only method
        # ProxyBackend accepts.
        client.sendall(bytes([0x05, 0x01, 0x02]))
        assert client.recv(2) == bytes([0x05, 0xFF])
        client.close()
    finally:
        harness.close()


def test_socks5_bind_command_rejected_not_connect() -> None:
    harness = _harness(allow=["example.com"])
    try:
        client = harness.open_client(b"S")
        client.sendall(bytes([0x05, 0x01, 0x00]))
        assert client.recv(2) == bytes([0x05, 0x00])
        target = b"example.com"
        # CMD=0x02 (BIND), unsupported -- only CONNECT (0x01) is implemented.
        request = (
            bytes([0x05, 0x02, 0x00, 0x03, len(target)]) + target + (443).to_bytes(2, "big"))
        client.sendall(request)
        reply = client.recv(10)
        assert reply[1] == 0x07  # command not supported.
        client.close()
    finally:
        harness.close()


def test_socks5_malformed_greeting_closes_without_reply() -> None:
    harness = _harness()
    try:
        client = harness.open_client(b"S")
        client.sendall(b"\x04\x01\x00")  # wrong VER byte.
        client.settimeout(1.0)
        # Connection closed with no bytes ever sent back -- a clean EOF (b"") on most transports,
        # but a connected AF_UNIX socketpair can also surface the peer's close as ECONNRESET;
        # either way is "refused without a valid reply", never a real protocol reply.
        try:
            assert client.recv(16) == b""
        except ConnectionResetError:
            pass
        client.close()
    finally:
        harness.close()


# --- HTTP CONNECT ---


def test_http_connect_allowed_relays_bytes(echo_server: Callable[[], int]) -> None:
    harness = _harness(allow=["127.0.0.1"])
    try:
        client = harness.open_client(b"H")
        port = echo_server()
        client.sendall(f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
        response = client.recv(256)
        assert b"200" in response

        client.sendall(b"ping")
        assert client.recv(64) == b"PING"
        client.close()
    finally:
        harness.close()


def test_http_connect_denied_domain_refused_and_recorded() -> None:
    harness = _harness(deny=["denied.example"])
    try:
        client = harness.open_client(b"H")
        client.sendall(b"CONNECT denied.example:443 HTTP/1.1\r\nHost: denied.example\r\n\r\n")
        response = client.recv(256)
        assert b"403" in response
        client.close()
    finally:
        time.sleep(0.1)
        assert harness.recorder.domains == ["denied.example"]
        harness.close()


def test_http_connect_malformed_request_closes_without_reply() -> None:
    harness = _harness()
    try:
        client = harness.open_client(b"H")
        client.sendall(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")  # not CONNECT.
        client.settimeout(1.0)
        assert client.recv(16) == b""
        client.close()
    finally:
        harness.close()


def test_http_connect_bracketed_ipv6_target_parsed() -> None:
    harness = _harness(deny=["::1"])
    try:
        client = harness.open_client(b"H")
        client.sendall(b"CONNECT [::1]:443 HTTP/1.1\r\nHost: [::1]\r\n\r\n")
        response = client.recv(256)
        assert b"403" in response
        client.close()
    finally:
        time.sleep(0.1)
        assert harness.recorder.domains == ["::1"]
        harness.close()


# --- BlockedDomainRecorder ---


def test_blocked_domain_recorder_dedupes_and_resets() -> None:
    recorder = BlockedDomainRecorder()
    recorder.record("a.example")
    recorder.record("a.example")
    recorder.record("b.example")
    assert recorder.domains == ["a.example", "b.example"]
    recorder.reset()
    assert recorder.domains == []


# --- proxy_env_vars / relay stub / bootstrap script ---


def test_proxy_env_vars_shape() -> None:
    env = proxy_env_vars()
    assert env["HTTP_PROXY"] == env["http_proxy"]
    assert env["HTTP_PROXY"].startswith("http://127.0.0.1:")
    assert env["ALL_PROXY"].startswith("socks5h://127.0.0.1:")
    assert env["NO_PROXY"] == ""
    assert env["no_proxy"] == ""


def test_relay_stub_source_is_valid_python() -> None:
    compile(_RELAY_STUB_SOURCE, "<relay-stub>", "exec")


def test_bootstrap_script_shape() -> None:
    script = _bootstrap_script("/usr/bin/python3", 42, 10800, 10801)
    assert script.count(_RELAY_HEREDOC_DELIM) == 2
    assert "/usr/bin/python3" in script
    assert "mkfifo" in script
    assert "disown" in script
    assert "2>/dev/null" in script
    assert " 42 127.0.0.1 10800 10801 " in script


def test_bootstrap_script_quotes_interpreter_path_with_special_characters() -> None:
    script = _bootstrap_script("/path with spaces/python3", 42, 10800, 10801)
    assert "'/path with spaces/python3'" in script
