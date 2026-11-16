import http.server
import socket
import threading
import unittest
from unittest import mock

from piggyback import proxy as proxy_module
from piggyback.proxy import EgressProxy

from .test_netpolicy import infos


class Hello(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "5")
        self.end_headers()
        self.wfile.write(b"hello")

    def log_message(self, *args):
        pass


def send(port, raw):
    # A plain socket, not socket.create_connection: tests patch that name.
    with socket.socket() as s:
        s.settimeout(10)
        s.connect(("127.0.0.1", port))
        s.sendall(raw)
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    return data


class EgressProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hello)
        threading.Thread(target=cls.origin.serve_forever, daemon=True).start()
        cls.origin_url = f"http://127.0.0.1:{cls.origin.server_address[1]}/"

    @classmethod
    def tearDownClass(cls):
        cls.origin.shutdown()
        cls.origin.server_close()

    def setUp(self):
        self.proxy = EgressProxy(allow_loopback=False)

    def tearDown(self):
        self.proxy.close()

    def test_plain_loopback_refused_and_recorded(self):
        reply = send(self.proxy.port, f"GET {self.origin_url} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        self.assertTrue(reply.startswith(b"HTTP/1.1 403"), reply)
        self.assertEqual(self.proxy.blocked, [self.origin_url])

    def test_query_string_not_recorded(self):
        reply = send(
            self.proxy.port, b"GET http://10.0.0.1/p?token=secret HTTP/1.1\r\nHost: x\r\n\r\n"
        )
        self.assertTrue(reply.startswith(b"HTTP/1.1 403"), reply)
        self.assertEqual(self.proxy.blocked, ["http://10.0.0.1/p"])

    def test_connect_to_private_refused_and_recorded(self):
        reply = send(
            self.proxy.port, b"CONNECT 10.0.0.1:443 HTTP/1.1\r\nHost: 10.0.0.1:443\r\n\r\n"
        )
        self.assertTrue(reply.startswith(b"HTTP/1.1 403"), reply)
        self.assertEqual(self.proxy.blocked, ["10.0.0.1:443"])

    def test_websocket_upgrade_to_private_refused_and_recorded(self):
        raw = (
            b"GET ws://10.0.0.1:1/ HTTP/1.1\r\nHost: 10.0.0.1:1\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
        )
        self.assertTrue(send(self.proxy.port, raw).startswith(b"HTTP/1.1 403"))
        self.assertEqual(self.proxy.blocked, ["ws://10.0.0.1:1/"])

    def test_non_http_scheme_refused(self):
        raw = b"GET file:///etc/passwd HTTP/1.1\r\nHost: x\r\n\r\n"
        self.assertTrue(send(self.proxy.port, raw).startswith(b"HTTP/1.1 403"))
        self.assertEqual(self.proxy.blocked, ["file:///etc/passwd"])

    def test_loopback_forwarded_with_flag(self):
        self.proxy.close()
        self.proxy = EgressProxy(allow_loopback=True)
        reply = send(self.proxy.port, f"GET {self.origin_url} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        self.assertIn(b" 200 OK", reply)
        self.assertTrue(reply.endswith(b"hello"))
        self.assertEqual(self.proxy.blocked, [])

    def test_name_resolved_once_and_connect_uses_checked_address(self):
        # The second answer would rebind to loopback; the proxy must never ask for it.
        answers = [infos("93.184.216.34"), infos("127.0.0.1")]
        with (
            mock.patch("socket.getaddrinfo", side_effect=answers) as lookup,
            mock.patch.object(
                proxy_module.socket, "create_connection", side_effect=OSError
            ) as dial,
        ):
            reply = send(self.proxy.port, b"GET http://rebind.example/ HTTP/1.1\r\nHost: r\r\n\r\n")
        self.assertEqual(lookup.call_count, 1)
        dial.assert_called_once_with(("93.184.216.34", 80), timeout=proxy_module.TIMEOUT_S)
        self.assertTrue(reply.startswith(b"HTTP/1.1 502"), reply)

    def test_mixed_answer_refused_without_connecting(self):
        answers = infos("93.184.216.34", "10.0.0.1")
        with (
            mock.patch("socket.getaddrinfo", return_value=answers),
            mock.patch.object(proxy_module.socket, "create_connection") as dial,
        ):
            reply = send(self.proxy.port, b"CONNECT mixed.example:443 HTTP/1.1\r\n\r\n")
        dial.assert_not_called()
        self.assertTrue(reply.startswith(b"HTTP/1.1 403"), reply)
        self.assertEqual(self.proxy.blocked, ["mixed.example:443"])


if __name__ == "__main__":
    unittest.main()
