"""`piggyback mcp` against the real engine: real measure() and Chromium, on a host with the browser.

Serves the loopback fixtures and runs `check` through the JSON-RPC loop. Skipped without the
measure extra, like test_measure_integration.
"""

import functools
import http.server
import importlib.util
import io
import json
import threading
import unittest
from pathlib import Path

if importlib.util.find_spec("playwright") is None:
    raise unittest.SkipTest("playwright not installed; run with the measure extra")

from piggyback import mcp  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


class McpIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURES))
        cls.site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.site.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.site.shutdown()
        cls.site.server_close()

    def test_check_on_the_loopback_build_reports_defaults(self):
        build = f"http://127.0.0.1:{self.site.server_address[1]}/effects.html"
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "check", "arguments": {}},
        }
        out = io.BytesIO()
        mcp.serve(io.BytesIO(json.dumps(request).encode() + b"\n"), out, mcp.Session([], build))
        result = json.loads(out.getvalue())["result"]
        self.assertFalse(result["isError"], result)
        text = result["content"][0]["text"]
        self.assertTrue(text.startswith(mcp.FRAMING))
        self.assertIn("## Defaults detected", text)


if __name__ == "__main__":
    unittest.main()
