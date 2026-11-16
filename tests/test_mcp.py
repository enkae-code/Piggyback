"""`piggyback mcp`: the JSON-RPC loop over real pipes, and the tools with `measure` stubbed.

The protocol-only cases run the real CLI in a subprocess. The tool cases run `serve` in-process
with an injected fake `measure`, so no browser is needed. Integration lives in test_mcp_integration.
"""

import copy
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

from piggyback import fingerprint as fp
from piggyback import mcp
from piggyback.measure import MeasureError

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "fingerprints"
REF_URL = "https://93.184.216.34/"  # a public literal: config validation needs no DNS
BUILD_URL = "http://127.0.0.1:9/"  # loopback, so the fake knows it is the build


def load(name):
    return fp.load(FIXTURES / name)


class FakeMeasure:
    """Stands in for measure(): the build fixture for loopback URLs, the reference otherwise."""

    def __init__(self, ref, build):
        self.ref, self.build, self.calls = ref, build, []

    def __call__(self, url, *, allow_loopback=False, **_):
        self.calls.append(url)
        return copy.deepcopy(self.build if allow_loopback else self.ref)


def exchange(session, *lines):
    """Feed request lines through serve() in-process; return the parsed reply lines."""
    raw = b"".join(
        (line if isinstance(line, bytes) else json.dumps(line).encode()) + b"\n" for line in lines
    )
    out = io.BytesIO()
    mcp.serve(io.BytesIO(raw), out, session)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def call(name, arguments=None, rid=1):
    params = {"name": name, "arguments": {} if arguments is None else arguments}
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": params}


def cli(*args, stdin=b""):
    return subprocess.run(
        [sys.executable, "-m", "piggyback", "mcp", *args],
        input=stdin,
        capture_output=True,
        cwd=ROOT,
        timeout=60,
    )


class Protocol(unittest.TestCase):
    def test_initialize_list_and_notification_over_real_pipes(self):
        raw = (
            b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
            b'{"protocolVersion":"2025-06-18","capabilities":{},'
            b'"clientInfo":{"name":"t","version":"0"}}}\n'
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        )
        done = cli("--ref", REF_URL, stdin=raw)
        self.assertEqual(done.returncode, 0, done.stderr)
        replies = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])  # the notification got no reply
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", replies[0]["result"]["capabilities"])
        tools = {tool["name"]: tool for tool in replies[1]["result"]["tools"]}
        self.assertEqual(set(tools), {"get_brief", "check"})
        for tool in tools.values():
            self.assertIs(tool["inputSchema"]["additionalProperties"], False)

    def test_unknown_method_tool_and_extra_arguments_are_json_rpc_errors(self):
        session = mcp.Session([], None)
        replies = exchange(
            session,
            {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
            call("fetch", rid=2),
            call("check", {"url": "http://10.0.0.1/"}, rid=3),
            call("get_brief", {"url": "http://10.0.0.1/"}, rid=4),
            call("get_brief", {"refresh": "yes"}, rid=5),
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": ["check"]},
        )
        self.assertEqual(
            [r["error"]["code"] for r in replies], [-32601, -32602, -32602, -32602, -32602, -32602]
        )

    def test_malformed_json_is_a_parse_error_and_the_server_keeps_running(self):
        replies = exchange(
            mcp.Session([], None), b"{not json", {"jsonrpc": "2.0", "id": 9, "method": "ping"}
        )
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertIsNone(replies[0]["id"])
        self.assertEqual(replies[1], {"jsonrpc": "2.0", "id": 9, "result": {}})

    def test_check_without_a_build_is_a_tool_error_with_the_framing_line(self):
        result = exchange(mcp.Session([], None), call("check"))[0]["result"]
        self.assertTrue(result["isError"])
        self.assertTrue(result["content"][0]["text"].startswith(mcp.FRAMING))
        self.assertIn("no build configured", result["content"][0]["text"])

    def test_bad_config_exits_non_zero_before_serving(self):
        bad = [
            [],
            ["--ref", "http://127.0.0.1:8000/"],
            ["--ref", "http://localhost:8000/"],
            ["--build", "http://10.0.0.1/"],
            ["--ref", REF_URL] * 4,
        ]
        for args in bad:
            with self.subTest(args=args):
                done = cli(*args)
                self.assertNotEqual(done.returncode, 0)
                self.assertEqual(done.stdout, b"")
                self.assertIn(b"mcp failed", done.stderr)


class Tools(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMeasure(load("ref.json"), load("build.json"))
        self.session = mcp.Session([REF_URL], BUILD_URL, self.fake)

    def text(self, reply):
        return reply["result"]["content"][0]["text"]

    def test_get_brief_starts_with_framing_and_is_cached_until_refresh(self):
        first = exchange(self.session, call("get_brief"))[0]["result"]
        self.assertFalse(first["isError"])
        self.assertTrue(first["content"][0]["text"].startswith(mcp.FRAMING))
        self.assertIn("# Design brief", first["content"][0]["text"])
        exchange(self.session, call("get_brief"))
        self.assertEqual(len(self.fake.calls), 1)
        exchange(self.session, call("get_brief", {"refresh": True}))
        self.assertEqual(len(self.fake.calls), 2)

    def test_check_lists_differences_then_marks_them_fixed(self):
        first = self.text(exchange(self.session, call("check"))[0])
        self.assertIn("## Differences", first)
        self.assertIn("①", first)
        self.assertNotIn("Fixed since", first)
        # The build now matches the reference, so every concept flagged last time is fixed.
        self.fake.build = copy.deepcopy(self.fake.ref)
        second = self.text(exchange(self.session, call("check"))[0])
        self.assertIn("No differences above the thresholds.", second)
        self.assertIn("## Fixed since the last check", second)
        self.assertIn("- Heading type", second)

    def test_check_without_references_reports_defaults_on_the_build(self):
        session = mcp.Session([], BUILD_URL, self.fake)
        reply = self.text(exchange(session, call("check"))[0])
        self.assertIn("## Defaults detected", reply)
        self.assertIn("Gradient text", reply)

    def test_measure_errors_are_tool_errors_with_the_framing_line(self):
        def refuse(url, **_):
            raise MeasureError("url is not a public http(s) address")

        session = mcp.Session([REF_URL], None, refuse)
        result = exchange(session, call("get_brief"))[0]["result"]
        self.assertTrue(result["isError"])
        self.assertTrue(result["content"][0]["text"].startswith(mcp.FRAMING))

    def test_hostile_page_strings_arrive_cleaned(self):
        hostile = load("hostile.json")
        # The page URL is the only page string that reaches the brief through clean() alone:
        # font names are cut to a plain-name pattern before clean() sees them.
        hostile["url"] += "\u200b\u202e"
        self.fake.ref = hostile
        text = self.text(exchange(self.session, call("get_brief"))[0])
        self.assertNotIn("<script>", text)
        self.assertNotIn("\u200b", text)
        self.assertNotIn("\u202e", text)
        for line in text.splitlines():
            self.assertFalse(line.startswith(("## Borrow", "## Avoid", "- run")), line)


if __name__ == "__main__":
    unittest.main()
