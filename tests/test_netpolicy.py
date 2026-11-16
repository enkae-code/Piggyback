import unittest

from piggyback.netpolicy import host_allowed, url_allowed

# Literal IPs and fixed names only: no DNS, so the table runs offline.
PUBLIC = ["93.184.216.34", "2606:4700:4700::1111"]
BLOCKED = [
    "127.0.0.1",
    "::1",
    "10.0.0.1",
    "192.168.1.1",
    "172.16.0.1",
    "169.254.1.1",
    "100.64.0.1",
    "fc00::1",
    "fe80::1",
    "0.0.0.0",
    "224.0.0.1",
    "::ffff:127.0.0.1",
    "::ffff:10.0.0.1",
    "2002:7f00:1::1",
    "localhost",
    "LOCALHOST.",
    "printer.local",
    "app.localhost",
    "",
]
BLOCKED_URLS = [
    "file:///etc/passwd",
    "data:text/html,<h1>x</h1>",
    "ftp://93.184.216.34/",
    "http://127.0.0.1:8000/",
    "http://10.0.0.1/",
    "http://[::1]/",
    "http://localhost/",
    "http:///nohost",
]


class NetPolicyTests(unittest.TestCase):
    def test_public_hosts_allowed(self):
        for host in PUBLIC:
            with self.subTest(host=host):
                self.assertTrue(host_allowed(host))
        self.assertTrue(url_allowed("https://93.184.216.34/page"))

    def test_private_hosts_blocked(self):
        for host in BLOCKED:
            with self.subTest(host=host):
                self.assertFalse(host_allowed(host))

    def test_blocked_urls(self):
        for url in BLOCKED_URLS:
            with self.subTest(url=url):
                self.assertFalse(url_allowed(url))

    def test_loopback_allowed_only_with_flag(self):
        self.assertTrue(host_allowed("127.0.0.1", allow_loopback=True))
        self.assertTrue(host_allowed("::1", allow_loopback=True))
        self.assertTrue(url_allowed("http://127.0.0.1:8123/", allow_loopback=True))
        self.assertTrue(host_allowed("::ffff:127.0.0.1", allow_loopback=True))
        # The flag permits loopback only; other private ranges stay blocked.
        for host in ("10.0.0.1", "192.168.1.1", "169.254.1.1", "100.64.0.1", "fc00::1"):
            with self.subTest(host=host):
                self.assertFalse(host_allowed(host, allow_loopback=True))
        # "localhost" stays blocked even with the flag.
        self.assertFalse(host_allowed("localhost", allow_loopback=True))


if __name__ == "__main__":
    unittest.main()
