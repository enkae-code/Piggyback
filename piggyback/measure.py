"""Page design fingerprint.

Chromium runs in a child process: Playwright's sync API is not thread-safe.
The child reads one JSON request on stdin and prints one JSON line on stdout. Nothing from the
page is ever put on a command line or evaluated as code: the page script is a fixed file.
"""

import contextlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, netpolicy
from .browser import launch

SCRIPT = (Path(__file__).parent / "measure.js").read_text(encoding="utf-8").strip().rstrip(";")
TOTAL_S = 90  # whole run, browser start included
NAV_MS = 30_000
SETTLE_MS = 5_000  # a page that never goes idle is still measured
MAX_BLOCKED = 50


class MeasureError(RuntimeError):
    pass


def measure(url, *, viewport=(1440, 900), allow_loopback=False, screenshot=None):
    if not netpolicy.url_allowed(url, allow_loopback):
        hint = " (use --allow-loopback for your own local build)" if not allow_loopback else ""
        raise MeasureError(f"url is not a public http(s) address{hint}")
    request = {
        "url": url,
        "viewport": list(viewport),
        "allow_loopback": allow_loopback,
        "screenshot": str(screenshot) if screenshot else None,
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "piggyback.measure"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        out, err = proc.communicate(json.dumps(request), timeout=TOTAL_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise MeasureError(f"measure timed out after {TOTAL_S}s") from None
    lines = out.strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else {"error": err.strip() or "no output"}
    except json.JSONDecodeError:
        result = {"error": "worker output was not JSON"}
    if "error" in result:
        raise MeasureError(result["error"])
    return result


def _capture(req):
    from playwright.sync_api import sync_playwright

    width, height = req["viewport"]
    allow_loopback = req["allow_loopback"]
    blocked = []

    def guard(route):
        # Runs for every request the page makes, including subresources, iframes and redirect hops.
        # ponytail: DNS is looked up here and again by Chromium, so rebinding between the two is
        # the known residual gap; WebSocket traffic is not routed by page.route at all.
        url = route.request.url
        if netpolicy.url_allowed(url, allow_loopback):
            route.continue_()
        else:
            if len(blocked) < MAX_BLOCKED:
                blocked.append(url[:500])
            route.abort("blockedbyclient")

    with sync_playwright() as p:
        browser, label = launch(p)  # fresh temporary profile per launch, removed on close
        try:
            ctx = browser.new_context(
                viewport={"width": width, "height": height},
                service_workers="block",
                accept_downloads=False,
            )
            ctx.route("**/*", guard)
            page = ctx.new_page()
            page.goto(req["url"], wait_until="load", timeout=NAV_MS)
            page.evaluate("() => document.fonts.ready")
            with contextlib.suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
            fingerprint = page.evaluate(f"({SCRIPT})()")
            if req["screenshot"]:
                page.screenshot(path=req["screenshot"], type="png")
        finally:
            browser.close()
    return {
        **fingerprint,
        "url": req["url"],
        "captured_with": f"piggyback {__version__}",
        "browser": label,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "blocked_requests": blocked,
        "screenshot": req["screenshot"],
    }


def _worker():
    req = json.loads(sys.stdin.read())
    try:
        result = _capture(req)
    except Exception as exc:  # Playwright and launch errors; the first line is enough
        text = str(exc).strip()
        print(json.dumps({"error": text.splitlines()[0] if text else repr(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(_worker())
