"""Local forward proxy: the only route from the browser to the network.

Chromium sends every request here. Plain http and ws arrive as absolute URIs; https and wss
arrive as CONNECT. netpolicy resolves the host once and checks every address; the socket then
connects to that literal address, so a second DNS answer (rebinding) or a redirect cannot reach
a private address. Refused targets get 403 and are recorded in `blocked`.
"""

import contextlib
import socket
import threading
from urllib.parse import urlsplit

from . import netpolicy

TIMEOUT_S = 30  # idle sockets close after this; a quiet WebSocket is dropped the same way
MAX_CONNECTIONS = 64  # ponytail: thread per connection with a cap; asyncio if pages need more
MAX_BLOCKED = 50
MAX_HEAD = 16384
FORBIDDEN = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


class EgressProxy:
    def __init__(self, allow_loopback):
        self.allow_loopback = allow_loopback
        self.blocked = []
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port = self._server.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def record(self, target):
        # The record is written into fingerprint.json; query strings can carry capability tokens.
        if len(self.blocked) < MAX_BLOCKED:
            self.blocked.append(target.split("?", 1)[0][:500])

    def close(self):
        # Open connections end on their own timeouts; the worker process exits right after.
        self._server.close()

    def _accept(self):
        while True:
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            if not self._slots.acquire(blocking=False):
                client.close()
                continue
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client):
        try:
            with client:
                client.settimeout(TIMEOUT_S)
                self._handle(client)
        except (OSError, ValueError):
            pass  # one bad connection never takes the proxy down
        finally:
            self._slots.release()

    def _handle(self, client):
        head, rest = _read_head(client)
        if head is None:
            return
        request_line, *headers = head.split(b"\r\n")
        method, target, version = request_line.decode("latin-1").split(" ")
        if method == "CONNECT":
            self._tunnel(client, target, rest)
        else:
            self._forward(client, (method, target, version), headers, rest)

    def _tunnel(self, client, target, rest):
        parts = urlsplit(f"//{target}")
        upstream = self._open(client, parts.hostname or "", parts.port or 443, target)
        if upstream is None:
            return
        with upstream:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            _relay(client, upstream, rest)

    def _forward(self, client, request, headers, rest):
        method, target, version = request
        parts = urlsplit(target)
        if parts.scheme not in ("http", "ws") or not parts.hostname:
            self.record(target)
            client.sendall(FORBIDDEN)
            return
        upstream = self._open(client, parts.hostname, parts.port or 80, target)
        if upstream is None:
            return
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        head = _origin_form(f"{method} {path} {version}", headers)
        with upstream:
            _relay(client, upstream, head + rest)

    def _open(self, client, host, port, shown):
        """Connect to a checked address, or answer 403/502 and return None."""
        address = netpolicy.resolve_allowed(host, self.allow_loopback)
        if address is None:
            self.record(shown)
            client.sendall(FORBIDDEN)
            return None
        try:
            return socket.create_connection((address, port), timeout=TIMEOUT_S)
        except OSError:
            client.sendall(BAD_GATEWAY)
            return None


def _origin_form(request_line, headers):
    # Strip proxy-only headers. One request per upstream connection: unless it is an upgrade
    # (WebSocket), ask the server to close after its response.
    lines = [h for h in headers if not h.lower().startswith(b"proxy-")]
    if not any(h.lower().startswith(b"upgrade:") for h in headers):
        lines = [h for h in lines if not h.lower().startswith(b"connection:")]
        lines.append(b"Connection: close")
    return request_line.encode("latin-1") + b"\r\n" + b"\r\n".join(lines) + b"\r\n\r\n"


def _read_head(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk or len(data) > MAX_HEAD:
            return None, b""
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    return head, rest


def _relay(client, upstream, first):
    upstream.sendall(first)
    back = threading.Thread(target=_copy, args=(upstream, client), daemon=True)
    back.start()
    _copy(client, upstream)
    back.join()


def _copy(src, dst):
    with contextlib.suppress(OSError):
        while data := src.recv(65536):
            dst.sendall(data)
    with contextlib.suppress(OSError):
        dst.shutdown(socket.SHUT_WR)
