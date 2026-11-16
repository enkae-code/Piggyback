"""Page design fingerprint.

Chromium runs in a child process: Playwright's sync API is not thread-safe.
The child reads one JSON request on stdin and prints one JSON line on stdout. Nothing from the
page is ever put on a command line or evaluated as code: the page script is a fixed file.
"""

import contextlib
import json
import os
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, netpolicy
from .browser import NO_WEBRTC, launch
from .proxy import EgressProxy

SCRIPT = (Path(__file__).parent / "measure.js").read_text(encoding="utf-8").strip().rstrip(";")
BYPASS_ENV = "PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK"
TOTAL_S = 90  # whole run, browser start included
NAV_MS = 30_000
SETTLE_MS = 5_000  # a page that never goes idle is still measured


class MeasureError(RuntimeError):
    pass


def check_url(url, allow_loopback=False):
    if netpolicy.url_allowed(url, allow_loopback):
        return
    hint = ""
    if not allow_loopback and netpolicy.url_allowed(url, allow_loopback=True):
        hint = " (use --allow-loopback for your own local build)"
    raise MeasureError(f"url is not a public http(s) address{hint}")


def measure(url, *, viewport=(1440, 900), allow_loopback=False, screenshot=None):
    check_url(url, allow_loopback)
    request = {
        "url": url,
        "viewport": list(viewport),
        "allow_loopback": allow_loopback,
        "screenshot": str(screenshot) if screenshot else None,
    }
    # Playwright's bypass switch would let Chromium skip the proxy for loopback.
    env = {k: v for k, v in os.environ.items() if k != BYPASS_ENV}
    proc = subprocess.Popen(
        [sys.executable, "-m", "piggyback.measure"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
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
    # The proxy is the enforcement point: every connection Chromium makes goes through it.
    with closing(EgressProxy(allow_loopback)) as egress, sync_playwright() as p:

        def guard(route):
            # Second layer for page requests, redirect hops included; the proxy is authoritative.
            url = route.request.url
            if netpolicy.url_allowed(url, allow_loopback):
                route.continue_()
            else:
                egress.record(url)
                route.abort("blockedbyclient")

        proxy = {"server": f"http://127.0.0.1:{egress.port}"}  # loopback is proxied by Playwright
        browser, label = launch(
            p, proxy=proxy
        )  # fresh temporary profile per launch, removed on close
        try:
            ctx = browser.new_context(
                viewport={"width": width, "height": height},
                service_workers="block",
                accept_downloads=False,
            )
            ctx.route("**/*", guard)
            ctx.add_init_script(NO_WEBRTC)
            page = ctx.new_page()
            page.goto(req["url"], wait_until="load", timeout=NAV_MS)
            page.evaluate("() => document.fonts.ready")
            with contextlib.suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
            fingerprint = page.evaluate(f"({SCRIPT})()")
            page.emulate_media(reduced_motion="reduce")
            fingerprint["reduced_motion"] = {
                "still_running": page.evaluate(
                    "() => document.getAnimations().filter((animation) => animation.playState === 'running').length"
                )
            }
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
        "blocked_requests": egress.blocked,
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
