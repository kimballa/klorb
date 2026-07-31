# © Copyright 2026 Aaron Kimball
"""Domain-gated network egress for a sandboxed `Bash` command: a host-side SOCKS5 + HTTP CONNECT
proxy backend (`ProxyBackend`) reachable only from inside that one sandbox's own private
loopback, via a small in-sandbox relay stub that hands off each accepted connection's raw fd to
this process over a `socketpair()` control channel established before the sandbox is launched.

See docs/specs/bash-tool-and-command-permissions.md's "Network egress (domain-gated proxy)"
section for the current-state design this module implements, and
docs/adrs/cross-sandbox-net-namespace-via-socketpair-fd-passing.md for why fd-passing over a
`socketpair()` is the mechanism that gets one end of a live channel across the sandbox's
`--unshare-net` boundary in the first place.

`start_sandbox_networking()` is the module's one real entry point: it creates the control-channel
`socketpair()`, starts a live `ProxyBackend` listening on the host side, and returns everything
`klorb.tools.bash.BashTool` needs to wire the sandbox side in — the env vars to fold into the
sandbox's `--setenv` set, the sandbox-side control-channel fd to `pass_fds`, and the bootstrap
shell snippet that writes out and launches the in-sandbox relay stub before the model's own
command runs.

The relay stub itself (`_RELAY_STUB_SOURCE`) is deliberately protocol-blind: it never parses
SOCKS5/HTTP bytes, only accepts connections on two fixed loopback ports and hands each one's fd
across the control channel tagged with which port it arrived on (`b"S"`/`b"H"`) via
`socket.send_fds`/`socket.recv_fds` (stdlib since Python 3.9) — all real protocol parsing and the
`bash_domain_rules` deny/ask/allow decision live once, host-side, in `ProxyBackend`.

**Known v1 limitation: the `HTTP_PROXY`/`HTTPS_PROXY` listener only understands `CONNECT`.** A
real HTTP client asked to fetch a *plain* `http://` URL through `HTTP_PROXY` sends an ordinary
forward-proxy request (`GET http://host/path HTTP/1.1` direct to the proxy, expecting it to relay
a real HTTP response) rather than a `CONNECT` — only an `https://` target (or an explicit
`--proxytunnel`-style override) makes a client `CONNECT` first. `ProxyBackend` never implements
forward-proxy request/response relaying, only `CONNECT` tunneling (see `_read_http_connect_
request`'s method check), matching this module's `CONNECT`-only, never-decrypts-the-tunnel
posture — a plain-`http://` request against this listener gets no reply at all and the client
sees a connection failure, not a domain-gated refusal. In practice this is a narrow gap:
`pip`/`npm`/`git`/registry traffic is HTTPS-only today, and `ALL_PROXY`'s SOCKS5 listener has no
such restriction (SOCKS5 is transport-agnostic once a domain is approved).
"""

import importlib.resources
import logging
import os
import shlex
import socket
import threading
from dataclasses import dataclass, field
from typing import Callable

from klorb.permissions.domain_access import evaluate_domain
from klorb.process_config import DEFAULT_BASH_NETWORK_HTTP_CONNECT_PORT, DEFAULT_BASH_NETWORK_SOCKS_PORT
from klorb.session import SessionConfig

logger = logging.getLogger(__name__)

PROXY_LOOPBACK_HOST = "127.0.0.1"
"""Loopback address the in-sandbox relay stub binds its two listeners on, inside the sandbox's
own private loopback (`bwrap --unshare-net` gives every sandboxed command a fresh, isolated
`lo`), so there is no possibility of colliding with a real port on the host or in another
concurrently-running sandbox."""

_RELAY_STUB_RESOURCE_NAME = "relay_stub.py"
_RELAY_STUB_SOURCE = (
    importlib.resources.files("klorb.resources").joinpath(_RELAY_STUB_RESOURCE_NAME).read_text())
"""Source for the in-sandbox relay stub, packaged as `klorb.resources.relay_stub` and loaded
once at import time -- see that module's own docstring. Written to a temp file and run from
there (`_bootstrap_script()`), rather than passed as a `python3 -c` argument, so embedding it
inside a bash script string needs no shell-quoting of the Python source itself."""

_HANDSHAKE_TIMEOUT_SECONDS = 15.0
"""How long `ProxyBackend` waits for a SOCKS5 greeting/request or an HTTP CONNECT request line
to arrive on a freshly-accepted connection before giving up on it -- generous enough for any
well-behaved client's own connect-then-immediately-write behavior, short enough that a
connection opened and then abandoned doesn't tie up a handler thread indefinitely."""

_CONNECT_TIMEOUT_SECONDS = 10.0
"""How long `ProxyBackend._connect()` waits for the real outbound TCP connection (to an
`"allow"`-verdict domain) before giving up and replying with a connection-refused/502 -- separate
from `_HANDSHAKE_TIMEOUT_SECONDS`, which bounds reading the client's own request instead."""

_RECV_CHUNK_BYTES = 65536

_RELAY_STUB_FILENAME = "/tmp/.klorb-relay-stub.py"
_READY_FIFO_PATH = "/tmp/.klorb-relay-ready"
_RELAY_HEREDOC_DELIM = "KLORB_RELAY_STUB_EOF"


def _bootstrap_script(
    python_executable: str, ctrl_fd: int, socks_port: int, http_connect_port: int,
) -> str:
    """The bash snippet `BashTool` runs as the first step of a sandboxed shell's life (before the
    model's own command, for a one-shot invocation; once, at spawn, for a persistent shell): write
    `_RELAY_STUB_SOURCE` to a file via a quoted heredoc (so bash performs no `$`/backtick
    expansion on its contents), launch it in the background with `python_executable` and its
    positional arguments (`ctrl_fd`, the loopback host, `socks_port`, `http_connect_port`, and
    the ready-FIFO path -- see `klorb.resources.relay_stub`), then block on reading
    `_READY_FIFO_PATH` -- a POSIX FIFO `open()` for reading blocks until a writer opens it, which
    the relay stub only does once both its listeners are actually bound -- so the real command
    never starts racing against a relay that isn't listening yet. The FIFO must be created
    (`mkfifo`) before the relay is launched, since opening a not-yet-existing path is just an
    ordinary error, not a wait-for-creation.

    The relay is `disown`ed right after backgrounding: without it, a persistent shell's `jobs -p`
    would list the relay forever, indistinguishable from a real model-started background job --
    which would make `klorb.tools.bash.BashTool._reconcile_sandbox`'s `has_live_jobs()` check
    always see "live work" and permanently refuse the reconcile-on-grow rebuild it exists to do
    (see docs/adrs/rebuild-persistent-sandbox-only-when-no-live-jobs.md). `disown` only removes
    the job from the shell's own job table; the process stays alive, still in the same process
    group, so it's still reaped by the same `SIGKILL`-the-process-group teardown every other
    sandboxed child gets (verified empirically, the same way this project verifies other
    process-lifecycle assumptions rather than assuming them from documentation).

    The launch itself is wrapped in a `{ ... } 2>/dev/null` group: the one-shot execution path
    runs its sandboxed shell with `-i` (see `klorb.tools.bash.BashTool._execute`), which prints a
    `[1] <pid>` job-start announcement to stderr the instant a command is backgrounded --
    `disown` only stops the *later* "Done"/"Terminated" notification, not this one, and an
    unsuppressed `[1] <pid>` line would otherwise land in front of the model's own command output
    on every single sandboxed invocation. Scoping the redirect to just this one group (rather
    than, say, `set +m` for the whole shell) leaves a real model-started background job's own
    `[N] <pid>` announcement intact, exactly as it prints today.
    """
    return f"""\
cat > {_RELAY_STUB_FILENAME} <<'{_RELAY_HEREDOC_DELIM}'
{_RELAY_STUB_SOURCE}{_RELAY_HEREDOC_DELIM}
mkfifo {_READY_FIFO_PATH}
{{ {shlex.quote(python_executable)} {_RELAY_STUB_FILENAME} \
{ctrl_fd} {PROXY_LOOPBACK_HOST} {socks_port} {http_connect_port} {_READY_FIFO_PATH} & }} 2>/dev/null
disown
read -r _klorb_relay_ready < {_READY_FIFO_PATH}
rm -f {_READY_FIFO_PATH}
"""


def proxy_env_vars(
    socks_port: int = DEFAULT_BASH_NETWORK_SOCKS_PORT,
    http_connect_port: int = DEFAULT_BASH_NETWORK_HTTP_CONNECT_PORT,
) -> dict[str, str]:
    """The `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` (and lowercase forms) env vars pointing at the
    relay's two loopback ports, plus `NO_PROXY`/`no_proxy` forced empty so a value inherited from
    `shareEnv`/`setEnv` can't punch a bypass hole -- both an HTTP CONNECT proxy (`HTTP_PROXY`/
    `HTTPS_PROXY` -- understood natively by `pip`/`npm`/etc. with no optional dependency) and a
    SOCKS5 one (`ALL_PROXY`, `socks5h://` so DNS resolution happens host-side) are stood up, on
    two separate ports rather than one multiplexed port. Only meaningful once folded into a
    sandbox's own `--setenv` set -- see `SandboxNetworking.env_vars` and
    `klorb.tools.bash.build_bash_env`."""
    http_url = f"http://{PROXY_LOOPBACK_HOST}:{http_connect_port}"
    socks_url = f"socks5h://{PROXY_LOOPBACK_HOST}:{socks_port}"
    return {
        "http_proxy": http_url, "HTTP_PROXY": http_url,
        "https_proxy": http_url, "HTTPS_PROXY": http_url,
        "all_proxy": socks_url, "ALL_PROXY": socks_url,
        "no_proxy": "", "NO_PROXY": "",
    }


class BlockedDomainRecorder:
    """Thread-safe, deduplicating collector for domains `ProxyBackend` refused during one
    sandboxed command's lifetime. `ProxyBackend`'s connection-handler threads call `record()`
    concurrently (a shell script can open several connections in parallel); `BashTool` reads
    `domains` back once the command finishes, to populate its `blocked_domains` response field. A
    persistent shell's single `SandboxNetworking` (and thus this recorder) outlives any one
    command, so `BashTool` calls `reset()` immediately before each `run_command()` so one
    command's response never reports a prior command's blocked domains."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._domains: list[str] = []

    def record(self, domain: str) -> None:
        with self._lock:
            if domain not in self._domains:
                self._domains.append(domain)

    @property
    def domains(self) -> list[str]:
        with self._lock:
            return list(self._domains)

    def reset(self) -> None:
        with self._lock:
            self._domains.clear()


def _recv_exact(sock: socket.socket, count: int) -> bytes | None:
    """Read exactly `count` bytes from `sock`, or `None` on EOF/short read -- the building block
    every fixed-width SOCKS5 field read (`_socks5_handshake`/`_socks5_read_request`) uses, since a
    single `recv()` call is never guaranteed to return a full multi-byte field in one shot."""
    chunks = bytearray()
    while len(chunks) < count:
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)


_SOCKS5_REP_SUCCEEDED = 0x00
_SOCKS5_REP_NOT_ALLOWED_BY_RULESET = 0x02
_SOCKS5_REP_CONNECTION_REFUSED = 0x05
_SOCKS5_REP_COMMAND_NOT_SUPPORTED = 0x07
_SOCKS5_REP_ADDRESS_TYPE_NOT_SUPPORTED = 0x08
"""RFC 1928 `REP` status codes `_socks5_reply()` sends -- the SOCKS5 replies this proxy actually
uses; RFC 1928 defines several more (general server failure, network/host unreachable, TTL
expired) that no code path here ever has reason to send."""


def _socks5_handshake(conn: socket.socket) -> bool:
    """Read a SOCKS5 greeting (RFC 1928 `VER`/`NMETHODS`/`METHODS`) and reply, accepting only the
    no-auth method (`0x00`) -- no other method is ever offered, since this proxy's own
    domain-gating is the only access control it needs; RFC 1928's username/password auth would
    just be one more secret to manage for no additional protection. Returns whether the greeting
    was valid and no-auth was accepted; `False` means the caller should give up on this
    connection without reading a request."""
    header = _recv_exact(conn, 2)
    if header is None or header[0] != 0x05:
        return False
    methods = _recv_exact(conn, header[1])
    if methods is None:
        return False
    if 0x00 not in methods:
        conn.sendall(bytes([0x05, 0xFF]))
        return False
    conn.sendall(bytes([0x05, 0x00]))
    return True


def _socks5_reply(conn: socket.socket, rep: int) -> None:
    """Send a SOCKS5 reply (RFC 1928 `VER`/`REP`/`RSV`/`ATYP`/`BND.ADDR`/`BND.PORT`) with `rep` as
    the status code and a placeholder `0.0.0.0:0` bind address -- real clients tunneling HTTP(S)
    through a `CONNECT`-only proxy don't act on the bind address, only the status byte."""
    conn.sendall(bytes([0x05, rep, 0x00, 0x01]) + socket.inet_aton("0.0.0.0") + (0).to_bytes(2, "big"))


def _socks5_read_request(conn: socket.socket) -> tuple[str, int] | None:
    """Read a SOCKS5 request (RFC 1928 `VER`/`CMD`/`RSV`/`ATYP`/`DST.ADDR`/`DST.PORT`) and return
    `(host, port)` for a `CONNECT` (`CMD=0x01`) request naming an IPv4, IPv6, or domain-name
    target -- `socks5h://` (resolution-deferred) means a well-behaved client sends `ATYP=0x03`
    (domain name) so DNS happens host-side (see this module's own docstring), but IPv4/IPv6
    literals are accepted too, for a client given an IP literal directly. `None` on any parse
    failure, or once a non-`CONNECT`/unsupported-`ATYP` reply has already been sent -- the caller
    should give up on the connection either way."""
    header = _recv_exact(conn, 4)
    if header is None or header[0] != 0x05:
        return None
    cmd, atyp = header[1], header[3]
    if cmd != 0x01:
        _socks5_reply(conn, _SOCKS5_REP_COMMAND_NOT_SUPPORTED)  # BIND/UDP ASSOCIATE unsupported.
        return None
    if atyp == 0x01:
        addr_bytes = _recv_exact(conn, 4)
        if addr_bytes is None:
            return None
        host = socket.inet_ntoa(addr_bytes)
    elif atyp == 0x03:
        length_byte = _recv_exact(conn, 1)
        if length_byte is None:
            return None
        name = _recv_exact(conn, length_byte[0])
        if name is None:
            return None
        host = name.decode("utf-8", errors="replace")
    elif atyp == 0x04:
        addr_bytes = _recv_exact(conn, 16)
        if addr_bytes is None:
            return None
        host = socket.inet_ntop(socket.AF_INET6, addr_bytes)
    else:
        _socks5_reply(conn, _SOCKS5_REP_ADDRESS_TYPE_NOT_SUPPORTED)
        return None
    port_bytes = _recv_exact(conn, 2)
    if port_bytes is None:
        return None
    return host, int.from_bytes(port_bytes, "big")


def _read_http_connect_request(conn: socket.socket) -> tuple[str, int] | None:
    """Read an HTTP `CONNECT host:port HTTP/1.1` request line (and discard the headers that
    follow it, up through the blank line terminating them) and return `(host, port)` -- `None` on
    a malformed request, a non-`CONNECT` method, or a request line larger than 8KiB (a real
    `CONNECT` request line is always short; a longer one is not a client this proxy needs to
    support)."""
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf.extend(chunk)
        if len(buf) > 8192:
            return None
    head = bytes(buf).split(b"\r\n\r\n", 1)[0]
    first_line = head.split(b"\r\n", 1)[0]
    parts = first_line.split(b" ")
    if len(parts) < 2 or parts[0].upper() != b"CONNECT":
        return None
    target = parts[1].decode("utf-8", errors="replace")
    if target.startswith("["):
        host_part, sep, port_part = target.rpartition("]:")
        if not sep:
            return None
        host = host_part[1:]
    else:
        host, sep, port_part = target.rpartition(":")
        if not sep:
            return None
    try:
        port = int(port_part)
    except ValueError:
        return None
    return host, port


def _relay_bidirectional(a: socket.socket, b: socket.socket) -> None:
    """Pump bytes between `a` and `b` in both directions, concurrently, until either side closes
    -- the tunnel body of an approved `CONNECT` (SOCKS5 or HTTP), run only after the domain
    decision and the handshake/reply are already done. Blocks until both directions have finished
    (each stops independently, once its own read side hits EOF or errors)."""
    def pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(_RECV_CHUNK_BYTES)
                if not data:
                    return
                dst.sendall(data)
        except OSError:
            return
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    t1 = threading.Thread(target=pump, args=(a, b), daemon=True)
    t2 = threading.Thread(target=pump, args=(b, a), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()


class ProxyBackend:
    """The host-side half of one sandboxed command's network-egress proxy: owns the host end of
    the control-channel `socketpair()`, receives each connection the in-sandbox relay stub
    accepts (via `socket.recv_fds`, tagged `b"S"`/`b"H"` for which of the stub's two listeners it
    came from), speaks just enough SOCKS5 or HTTP CONNECT to extract the target `host:port`,
    evaluates the host against `session_config.bash_domain_rules` (read fresh on every
    connection, never cached, so a mid-command grant takes effect on the very next connection),
    and either relays bytes bidirectionally (an `"allow"` verdict) or replies with a
    protocol-appropriate refusal and closes (`"deny"` or unresolved `"ask"` -- both fail closed
    identically in v1, since there is no live-ask mechanism that could reach a proxy connection
    mid-handshake).
    """

    def __init__(
        self, control_sock: socket.socket, session_config: SessionConfig,
        on_blocked: Callable[[str], None],
    ) -> None:
        self._control_sock = control_sock
        self._session_config = session_config
        self._on_blocked = on_blocked
        self._thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="klorb-proxy-accept")

    def start(self) -> None:
        logger.debug("ProxyBackend starting accept loop")
        self._thread.start()

    def close(self) -> None:
        """Close the host-side control-channel socket, so `_accept_loop`'s next `recv_fds()`
        call returns EOF and the accept thread exits. Safe to call more than once."""
        try:
            self._control_sock.close()
        except OSError:
            pass

    def _accept_loop(self) -> None:
        while True:
            try:
                data, fds, _flags, _addr = socket.recv_fds(self._control_sock, 1, 1)
            except OSError:
                return
            if not fds:
                logger.debug("ProxyBackend control channel closed; stopping accept loop")
                return
            tag = data[:1]
            conn = socket.socket(fileno=fds[0])
            threading.Thread(
                target=self._handle_connection, args=(conn, tag), daemon=True,
                name="klorb-proxy-conn").start()

    def _handle_connection(self, conn: socket.socket, tag: bytes) -> None:
        try:
            conn.settimeout(_HANDSHAKE_TIMEOUT_SECONDS)
            if tag == b"S":
                self._serve_socks5(conn)
            else:
                self._serve_http_connect(conn)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _evaluate(self, host: str) -> str:
        return evaluate_domain(self._session_config.bash_domain_rules, host.lower())

    def _connect(self, host: str, port: int) -> socket.socket | None:
        try:
            return socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT_SECONDS)
        except OSError as exc:
            logger.debug("ProxyBackend outbound connect to %s:%d failed: %s", host, port, exc)
            return None

    def _serve_socks5(self, conn: socket.socket) -> None:
        if not _socks5_handshake(conn):
            return
        request = _socks5_read_request(conn)
        if request is None:
            return
        host, port = request
        if self._evaluate(host) != "allow":
            logger.info("ProxyBackend refused SOCKS5 CONNECT to %s:%d (bashDomains)", host, port)
            self._on_blocked(host)
            _socks5_reply(conn, _SOCKS5_REP_NOT_ALLOWED_BY_RULESET)
            return
        outbound = self._connect(host, port)
        if outbound is None:
            _socks5_reply(conn, _SOCKS5_REP_CONNECTION_REFUSED)
            return
        _socks5_reply(conn, _SOCKS5_REP_SUCCEEDED)
        conn.settimeout(None)
        try:
            _relay_bidirectional(conn, outbound)
        finally:
            outbound.close()

    def _serve_http_connect(self, conn: socket.socket) -> None:
        request = _read_http_connect_request(conn)
        if request is None:
            return
        host, port = request
        if self._evaluate(host) != "allow":
            logger.info("ProxyBackend refused HTTP CONNECT to %s:%d (bashDomains)", host, port)
            self._on_blocked(host)
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return
        outbound = self._connect(host, port)
        if outbound is None:
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        conn.settimeout(None)
        try:
            _relay_bidirectional(conn, outbound)
        finally:
            outbound.close()


@dataclass
class SandboxNetworking:
    """Everything `klorb.tools.bash.BashTool` needs to wire up one live sandbox's network egress:
    a running `ProxyBackend` (`backend`), the `BlockedDomainRecorder` it reports refusals to
    (`recorder`), the sandbox-side control-channel fd to `subprocess.Popen(..., pass_fds=...)`
    (`sandbox_fd`), the env vars to fold into the sandbox's own `--setenv` set (`env_vars`), and
    the bootstrap shell snippet that launches the in-sandbox relay stub before the model's own
    command runs (`bootstrap_script`). One instance per live sandbox -- a one-shot command's own
    `bwrap` invocation, or a persistent shell's (kept alongside it, closed when the shell is)."""

    backend: ProxyBackend
    recorder: BlockedDomainRecorder
    sandbox_fd: int
    env_vars: dict[str, str]
    bootstrap_script: str
    _sandbox_sock: socket.socket = field(repr=False)
    _closed: bool = field(default=False, repr=False)

    def release_sandbox_fd(self) -> None:
        """Close this (klorb) process's own copy of the sandbox-side control-channel socket, once
        `subprocess.Popen(..., pass_fds=(self.sandbox_fd,))` has forked the sandboxed process --
        `fork()` gives the child an independent, separately-refcounted copy of the same fd, so
        closing the parent's copy here doesn't affect the relay stub's own. Safe to call more than
        once; idempotent."""
        try:
            self._sandbox_sock.close()
        except OSError:
            pass

    def close(self) -> None:
        """End this sandbox's network-egress proxy entirely: release the sandbox-side fd (see
        `release_sandbox_fd`, a no-op if already released) and close the host-side control socket,
        which stops `ProxyBackend`'s accept loop on its next `recv_fds()` call. A no-op past the
        first call, so callers never need to track whether they've already closed one of these."""
        if self._closed:
            return
        self._closed = True
        self.release_sandbox_fd()
        self.backend.close()


def start_sandbox_networking(
    session_config: SessionConfig, python_executable: str,
    socks_port: int = DEFAULT_BASH_NETWORK_SOCKS_PORT,
    http_connect_port: int = DEFAULT_BASH_NETWORK_HTTP_CONNECT_PORT,
) -> SandboxNetworking:
    """Create the control-channel `socketpair()`, start a live `ProxyBackend` listening on the
    host side, and return everything `BashTool` needs to wire the sandbox side of one live
    sandbox's network egress in. `python_executable` is the interpreter the in-sandbox relay stub
    is run with -- the klorb process's own (`sys.executable`), reached via a PATH-derived top-up
    bind (see `klorb.tools.bash._bwrap_prefix`), never a `python3` klorb happens to find on the
    sandboxed `PATH` -- see `klorb.resources.relay_stub`'s own docstring for why. `socks_port`/
    `http_connect_port` come from `ProcessConfig.bash_network_socks_port`/
    `bash_network_http_connect_port` (`tools.bash.network.socksPort`/`httpConnectPort`), so a
    host on which those fixed loopback ports are already taken can move the relay elsewhere."""
    host_sock, sandbox_sock = socket.socketpair()
    os.set_inheritable(sandbox_sock.fileno(), True)
    recorder = BlockedDomainRecorder()
    backend = ProxyBackend(host_sock, session_config, recorder.record)
    backend.start()
    logger.debug(
        "start_sandbox_networking: control socketpair host_fd=%d sandbox_fd=%d",
        host_sock.fileno(), sandbox_sock.fileno())
    return SandboxNetworking(
        backend=backend, recorder=recorder, sandbox_fd=sandbox_sock.fileno(),
        env_vars=proxy_env_vars(socks_port, http_connect_port),
        bootstrap_script=_bootstrap_script(
            python_executable, sandbox_sock.fileno(), socks_port, http_connect_port),
        _sandbox_sock=sandbox_sock)
