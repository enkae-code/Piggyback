"""Page design fingerprint.

Chromium runs in a child process: Playwright's sync API is not thread-safe.
The child reads one JSON request on stdin and prints one JSON line on stdout. Nothing from the
page is ever put on a command line or evaluated as code: the page script is a fixed file.
"""

import contextlib
import json
import os
import platform
import secrets
import stat
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

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
    """The URL to fetch (localhost rewritten to 127.0.0.1), or MeasureError."""
    url = netpolicy.local_name(url)
    if netpolicy.url_allowed(url, allow_loopback):
        return url
    hint = ""
    if not allow_loopback and netpolicy.url_allowed(url, allow_loopback=True):
        hint = " (use --allow-loopback for your own local build)"
    raise MeasureError(f"url is not a public http(s) address{hint}")


def receipt_refusal(status, requested, final):
    """Why the answer cannot stand as the reference, or None. Only 2xx on the same host passes."""
    final_host = urlsplit(final).hostname or "unknown host"
    if status is None:
        return "page gave no HTTP response"
    if not 200 <= status < 300:
        return f"page answered HTTP {status} from {final_host}: measure a page that loads with 2xx"
    if final_host != urlsplit(requested).hostname:
        return f"page redirected to {final}, not the host you gave: measure that address"
    return None


def _refuse_if_moved(status, req, page):
    refusal = receipt_refusal(status, req["url"], page.url)
    if refusal:
        raise MeasureError(refusal)


def refuse_links(*paths):
    """MeasureError if a path is a symbolic link: output never follows a link out of its folder."""
    for path in paths:
        if os.path.islink(path):
            raise MeasureError(
                f"{Path(path).name} is a symbolic link; refusing to write through it"
            )


def _temp_beside(path):
    """Empty temp file beside path under a random name, created O_EXCL so it is never an existing
    file or link. The umask sets its mode; an existing regular file at path only narrows it."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    os.close(
        os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o666)
    )
    try:
        try:
            existing = os.lstat(path).st_mode
        except FileNotFoundError:
            existing = 0
        if stat.S_ISREG(existing):
            os.chmod(tmp, os.lstat(tmp).st_mode & existing & 0o777)
    except BaseException:
        os.unlink(tmp)
        raise
    return str(tmp)


def publish_json(path, data):
    """Replace path with JSON text in one rename: a crash never leaves a partial file."""
    tmp = _temp_beside(path)
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)  # replaces a link at path, never writes through it
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_measure(url, out_dir, *, viewport=(1440, 900), allow_loopback=False):
    """Measure one page into out_dir: fingerprint.json and screenshot.png. Raises MeasureError."""
    check_url(url, allow_loopback)
    out_dir = Path(out_dir)
    shot = out_dir / "screenshot.png"
    fingerprint = out_dir / "fingerprint.json"
    refuse_links(shot, fingerprint)  # before the browser runs
    out_dir.mkdir(parents=True, exist_ok=True)
    result = measure(url, viewport=viewport, allow_loopback=allow_loopback, screenshot=shot)
    publish_json(fingerprint, result)  # last: its presence marks the folder complete
    return fingerprint, shot


def measure(url, *, viewport=(1440, 900), allow_loopback=False, screenshot=None, evidence=None):
    """Fingerprint one page. evidence: optional path for a DOM snapshot (Python API only)."""
    url = check_url(url, allow_loopback)
    request = {
        "url": url,
        "viewport": list(viewport),
        "allow_loopback": allow_loopback,
        "screenshot": str(screenshot) if screenshot else None,
        "evidence": str(evidence) if evidence else None,
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


EVIDENCE_STYLES = [
    "display",
    "visibility",
    "opacity",
    "font-size",
    "line-height",
    "letter-spacing",
    "font-weight",
    "font-family",
    "color",
    "background-color",
    "border-top-left-radius",
    "box-shadow",
    "padding-top",
    "padding-right",
    "padding-bottom",
    "padding-left",
]
PAGE_STATE = (
    "() => ({url: location.href, title: document.title, scroll: [scrollX, scrollY],"
    " viewport: [innerWidth, innerHeight],"
    " text: (document.body ? document.body.innerText : '').slice(0, 4000)})"
)


def _save_evidence(page, path):
    """DOM snapshot with computed styles and rects, from the same session, to a JSON file."""
    refuse_links(path)
    session = page.context.new_cdp_session(page)
    try:
        snapshot = session.send(
            "DOMSnapshot.captureSnapshot",
            {
                "computedStyles": EVIDENCE_STYLES,
                "includeDOMRects": True,
                "includePaintOrder": False,
            },
        )
    finally:
        session.detach()
    publish_json(path, {"styles": EVIDENCE_STYLES, "page": page.evaluate(PAGE_STATE), **snapshot})


def _capture_motion(page, screenshot, evidence=None):
    fingerprint = page.evaluate(f"({SCRIPT})()")
    if evidence:  # opt-in: before the screenshot and reduced-motion emulation, same session
        _save_evidence(page, evidence)
    if screenshot:
        page.screenshot(path=screenshot, type="png")
    page.emulate_media(reduced_motion="reduce")
    fingerprint["reduced_motion"] = {
        "still_running": page.evaluate(
            "() => document.getAnimations()"
            ".filter((animation) => animation.playState === 'running').length"
        )
    }
    return fingerprint


def _capture(req):
    shot = req["screenshot"]
    # Playwright writes a fresh temp file beside the final name. os.replace publishes it last, so
    # a link or hardlink at the final name is replaced, never written through.
    tmp = _temp_beside(shot) if shot else None
    try:
        result = _capture_page(req, tmp)
        if tmp:
            os.replace(tmp, shot)
        return result
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def _capture_page(req, tmp):
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
            response = page.goto(req["url"], wait_until="load", timeout=NAV_MS)
            status = response.status if response else None
            _refuse_if_moved(status, req, page)
            page.evaluate("() => document.fonts.ready")
            with contextlib.suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
            fingerprint = _capture_motion(page, tmp, req.get("evidence"))
            try:
                _refuse_if_moved(status, req, page)  # a script may redirect while the page settles
            except MeasureError:
                if req["screenshot"]:
                    Path(req["screenshot"]).unlink(missing_ok=True)
                raise
            captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            receipt = {
                "status": status,
                "final_url": page.url,
                "browser": label,
                "browser_version": browser.version,
                "os": f"{platform.system()} {platform.release()}",
                "viewport": {"width": width, "height": height},
                "captured_at": captured_at,
            }
        finally:
            browser.close()
    return {
        **fingerprint,
        "url": req["url"],
        "captured_with": f"piggyback {__version__}",
        "browser": label,
        "captured_at": captured_at,
        "capture": receipt,
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
