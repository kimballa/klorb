# © Copyright 2026 Aaron Kimball
"""In-sandbox relay stub for `klorb.sandbox.network`'s domain-gated network-egress proxy.
Deliberately protocol-blind: it never parses SOCKS5/HTTP bytes, only accepts connections on two
fixed loopback ports and hands each accepted connection's raw fd across a `socketpair()` control
channel to the host-side `klorb.sandbox.network.ProxyBackend`, tagged with which port it arrived
on (`b"S"`/`b"H"`) via `socket.send_fds` (stdlib since Python 3.9). All real protocol parsing and
the `bash_domain_rules` deny/ask/allow decision live once, host-side, in `ProxyBackend`.

Loaded as packaged resource text (`klorb.sandbox.network._RELAY_STUB_SOURCE`) and written into
the sandbox via a heredoc rather than imported, so it never runs inside the klorb process itself
-- see `klorb.sandbox.network._bootstrap_script`. Run with the klorb process's own Python
interpreter (never a `python3` the sandboxed `PATH` happens to find, so `socket.send_fds`'s
Python 3.9+ requirement is always satisfied) and given its parameters positionally on `sys.argv`,
since a packaged resource file has no way to have values interpolated into it the way an f-string
constant could: the control-channel fd, the loopback host, the SOCKS5 port, the HTTP CONNECT
port, and the ready-FIFO path to signal once both listeners are bound.
"""

import socket
import sys
import threading

_ctrl_fd, _loopback_host, _socks_port, _http_port, _ready_fifo = (
    int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])

_ctrl = socket.socket(fileno=_ctrl_fd)
_send_lock = threading.Lock()


def _listen(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((_loopback_host, port))
    s.listen(128)
    return s


def _accept_loop(listener: socket.socket, tag: bytes) -> None:
    while True:
        try:
            conn, _addr = listener.accept()
        except OSError:
            return
        try:
            with _send_lock:
                socket.send_fds(_ctrl, [tag], [conn.fileno()])
        except OSError:
            pass
        conn.close()


_socks_listener = _listen(_socks_port)
_http_listener = _listen(_http_port)
threading.Thread(target=_accept_loop, args=(_socks_listener, b"S"), daemon=True).start()
threading.Thread(target=_accept_loop, args=(_http_listener, b"H"), daemon=True).start()

with open(_ready_fifo, "w") as _f:
    _f.write("1")

threading.Event().wait()
