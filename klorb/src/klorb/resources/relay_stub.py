# © Copyright 2026 Aaron Kimball
"""In-sandbox relay stub for `klorb.sandbox.network`'s domain-gated network-egress proxy."""

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
