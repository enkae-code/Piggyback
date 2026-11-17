"""`piggyback` local screen: one page on 127.0.0.1 that measures a reference and an optional build.

Stdlib only. Every API call carries the per-launch token; Host and Origin must name this server.
Results live in memory for the session. Screenshots are served by opaque id from a temp dir.
"""

import ipaddress
import json
import os
import re
import secrets
import signal
import sys
import tempfile
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import brief as brief_mod
from . import defaults as defaults_mod
from . import gap as gap_mod
from . import netpolicy
from .glossary import TERMS
from .measure import MeasureError, check_url, measure

WEB = Path(__file__).parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/logo.svg": ("logo.svg", "image/svg+xml"),
    "/fonts/InstrumentSans-latin-var.woff2": ("fonts/InstrumentSans-latin-var.woff2", "font/woff2"),
}
CSP = (
    "default-src 'self'; img-src 'self' data: blob:; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'none'"
)
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}
TOKEN_HEADER = "X-Piggyback-Token"
MAX_BODY = 16 * 1024
MAX_URL = 2048
SHOT = re.compile(r"/api/shot/([0-9a-f]{32})\.png")
ESCAPED = re.compile(r"\\([\\`*_\[\]<>|#~!])")  # brief.py escapes markdown; the screen shows text
NOTE = "Differences use reference 1 (the first one listed)."  # shared by screen, MCP and CLI


def write_new(path, text):
    """Create a file that must not exist yet: BRIEF.md and DESIGN.md are never overwritten.

    O_EXCL also refuses a symlink, even a dangling one.
    """
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise FileExistsError(
            f"{path} already exists; Piggyback never overwrites it: "
            "move or delete it, then try again"
        ) from None
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)


def _plain(text):
    return ESCAPED.sub(r"\1", text)


def _on_this_machine(url):
    """True for a literal loopback address (localhost is already rewritten to 127.0.0.1). Only these
    builds get loopback reach: a hosted preview must not be able to call your local servers."""
    try:
        return ipaddress.ip_address((urlsplit(url).hostname or "").strip("[]")).is_loopback
    except ValueError:
        return False


def _shape(refs, build):
    """Raise ValueError for anything that is not one reference list plus an optional build."""
    if not isinstance(refs, list) or not all(isinstance(url, str) for url in refs):
        raise ValueError("references must be a list of URLs")
    if build is not None and not isinstance(build, str):
        raise ValueError("build must be a URL")
    _sizes(refs, build)


def _sizes(refs, build):
    if len(refs) > brief_mod.MAX_REFS:
        raise ValueError(f"give 1 to {brief_mod.MAX_REFS} references")
    if not refs and not build:
        raise ValueError("add a reference, your build, or both")
    if any(len(url) > MAX_URL for url in refs + [build or ""]):
        raise ValueError("URL is too long")


def _slots(body):
    """(references, build) from the request body. Raises ValueError with a user-facing message."""
    refs, build = body.get("references", []), body.get("build") or None
    _shape(refs, build)
    build = netpolicy.local_name(build) if build else None
    # Reference: public addresses only. Build: loopback too (your own localhost), still no LAN.
    for url in refs:
        check_url(url)
    if build:
        check_url(build, allow_loopback=True)
        # Loopback reach is only for a literal loopback address (see run); a name that resolves to
        # loopback (lvh.me, 127.1) would be measured without it and fail late with a CLI hint.
        if not _on_this_machine(build) and not netpolicy.url_allowed(build):
            raise ValueError("for a build on this machine, use localhost or 127.0.0.1")
    return refs, build


HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
PX = re.compile(r"(\d+(?:\.\d+)?)px")


def _visual(concept, detail):
    """Values the page draws (swatches, type sizes, radii) read from the measured detail text."""
    if concept == "palette":
        items = HEX.findall(detail)
        return {"kind": "swatches", "items": items} if items else None
    if concept in ("type_scale", "radius"):
        items = [float(value) for value in PX.findall(detail)]
        kind = "sizes" if concept == "type_scale" else "radii"
        return {"kind": kind, "items": items} if items else None
    return None


def _line(concept, detail, seen):
    """One glossary line. The why-it-matters sentence is shown once per concept on the page."""
    name, reason = TERMS[concept]
    why = None if concept in seen else reason
    seen.add(concept)
    text = _plain(detail)
    return {
        "concept": concept,
        "name": name,
        "detail": text,
        "why": why,
        "visual": _visual(concept, text),
    }


def _arrow(line):
    """Split 'build → reference' so the page can show the two values apart."""
    before, sep, after = line["detail"].partition(" → ")
    return {**line, "before": before, "after": after} if sep else line


def _view(fp):
    return {"url": fp["url"], "shot": fp["shot"], "viewport": fp.get("viewport")}


def _brief(fps, seen):
    rows = brief_mod.sections(fps)
    refs = [
        {
            "url": fp["url"],
            "shot": fp["shot"],
            "lines": [_line(concept, detail, seen) for concept, detail in entries],
        }
        for fp, (_, entries) in zip(fps, rows, strict=True)
    ]
    return {"state": "brief", "references": refs}, brief_mod.brief(fps)


def _defaults(build_fp, seen):
    hits = defaults_mod.find(build_fp)
    items = [
        {**_line(concept, detail, seen), "number": number}
        for number, (concept, detail) in enumerate(hits, 1)
    ]
    payload = {"state": "defaults", "build": _view(build_fp), "items": items}
    return payload, defaults_mod.render(hits)


def _compare(fps, build_fp, seen, before):
    """`before` is the last compare payload of a re-run on the same references, else None."""
    pins = gap_mod.gap(fps[0], build_fp)
    views = [
        {
            **_arrow(_line(pin.concept, pin.detail, seen)),
            "number": pin.number,
            "ref_box": pin.ref_box,
            "build_box": pin.build_box,
        }
        for pin in pins
        if pin.number is not None
    ]
    # A pin ticks when its concept was open and still compares now; ticks stay until reopened.
    prev = before or {"pins": [], "ticked": []}
    now = gap_mod.differing(pins)
    done = gap_mod.fixed({pin["concept"] for pin in prev["pins"]}, now, fps[0], build_fp)
    ticked = [pin for pin in prev["ticked"] if pin["concept"] not in now]
    ticked += [pin for pin in prev["pins"] if pin["concept"] in done]
    payload = {
        "state": "compare",
        "reference": _view(fps[0]),
        "build": _view(build_fp),
        "pins": views,
        "ticked": ticked,
        "not_found": [_plain(pin.detail) for pin in pins if pin.number is None],
        "note": NOTE,
    }
    return payload, brief_mod.brief(fps) + "\n" + gap_mod.render(pins) + f"\n{NOTE}\n"


def _render(fps, build_fp, before=None):
    """(state payload for the page, BRIEF.md text) for one run."""
    seen = set()
    if build_fp is None:
        return _brief(fps, seen)
    if not fps:
        return _defaults(build_fp, seen)
    return _compare(fps, build_fp, seen, before)


class Screen(ThreadingHTTPServer):
    def __init__(self, port, out_dir, shot_dir):
        super().__init__(("127.0.0.1", port), Handler)
        self.port = self.server_address[1]
        self.token = secrets.token_urlsafe(24)
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.out_dir = Path(out_dir).resolve()
        self.shot_dir = Path(shot_dir)
        self.busy = threading.Lock()  # one measure batch at a time
        self.shots = {}  # opaque id -> screenshot path: the reference captures and the last build
        self.refs_key = None  # reference URLs the captures in ref_fps were measured for
        self.ref_fps = []  # kept across re-runs, so references do not drift between them
        # The last compare payload: its open and ticked pins carry into a re-run.
        self.last_compare = None
        self.last_text = None  # BRIEF.md text of the last run

    def _shoot(self, url, allow_loopback, shown=None):
        shot_id = secrets.token_hex(16)
        path = self.shot_dir / f"{shot_id}.png"
        fp = measure(url, allow_loopback=allow_loopback, screenshot=path)
        self.shots[shot_id] = path
        return {**fp, "url": shown or fp["url"], "shot": shot_id}

    def run(self, refs, build, typed=None, rerun=False):
        # A re-run on the same references reuses their captures and the ticks; a new list or a fresh
        # measure measures them again.
        same = rerun and tuple(refs) == self.refs_key
        before = self.last_compare if same else None
        if not same:
            self.refs_key = None  # a failed capture leaves no half-kept list behind
            self.ref_fps = [self._shoot(url, False) for url in refs]
            self.refs_key = tuple(refs)
        # The page shows the build as the user typed it (localhost), not the address we measured.
        build_fp = self._shoot(build, _on_this_machine(build), typed) if build else None
        payload, text = _render(self.ref_fps, build_fp, before)
        keep = {fp["shot"] for fp in self.ref_fps} | ({build_fp["shot"]} if build_fp else set())
        for shot_id in [shot_id for shot_id in self.shots if shot_id not in keep]:
            self.shots.pop(shot_id).unlink(missing_ok=True)
        self.last_text = text
        self.last_compare = payload if payload["state"] == "compare" else None
        return payload


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self._admit(api=path.startswith("/api/")):
            return
        if path in STATIC:
            name, ctype = STATIC[path]
            return self._send(200, (WEB / name).read_bytes(), ctype)
        found = SHOT.fullmatch(path)
        shot = self.server.shots.get(found[1]) if found else None
        if shot is None:
            return self._error(404, "not found")
        return self._send(200, shot.read_bytes(), "image/png")

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self._admit(api=True):
            return
        if path not in ("/api/measure", "/api/brief"):
            return self._error(404, "not found")
        body = self._body()
        if body is None:
            return
        if path == "/api/brief":
            return self._brief()
        return self._measure(body)

    def _admit(self, api):
        """Host and Origin must name this server; API calls also need the session token."""
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in self.server.hosts or (origin is not None and origin != f"http://{host}"):
            self._error(403, "requests must come from the Piggyback page")
            return False
        token = self.headers.get(TOKEN_HEADER, "").encode("utf-8")
        if api and not secrets.compare_digest(token, self.server.token.encode("utf-8")):
            self._error(403, "missing or wrong session token: open the link piggyback printed")
            return False
        return True

    def _body(self):
        media = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if media != "application/json":
            self._error(415, "send application/json")
            return None
        length = self.headers.get("Content-Length", "")
        if not (length.isascii() and length.isdigit()) or int(length) > MAX_BODY:
            self._error(413, "request body is missing its length or is too large")
            return None
        try:
            body = json.loads(self.rfile.read(int(length)).decode("utf-8"))
        except (ValueError, RecursionError):
            self._error(400, "body is not valid JSON")
            return None
        if not isinstance(body, dict):
            self._error(400, "body must be a JSON object")
            return None
        return body

    def _measure(self, body):
        try:
            refs, build = _slots(body)
        except (ValueError, MeasureError) as exc:
            return self._error(400, str(exc))
        if not self.server.busy.acquire(blocking=False):
            return self._error(429, "a measure is already running")
        # The lock is released before any reply, so a client that has its answer can measure again.
        try:
            rerun = body.get("rerun") is True
            reply = (200, self.server.run(refs, build, body.get("build") or None, rerun))
        except MeasureError as exc:
            reply = (422, {"error": str(exc)})
        except Exception:  # noqa: BLE001 - answer the page instead of dropping the connection
            traceback.print_exc()
            reply = (500, {"error": "measure failed unexpectedly; see the terminal"})
        finally:
            self.server.busy.release()
        return self._json(*reply)

    def _brief(self):
        text = self.server.last_text
        if text is None:
            return self._error(409, "measure first, then copy the brief")
        path = self.server.out_dir / "BRIEF.md"
        try:
            write_new(path, text)
        except FileExistsError as exc:
            return self._error(409, str(exc))
        except OSError as exc:
            return self._error(500, f"could not write {path}: {exc.strerror}")
        return self._json(200, {"path": str(path), "text": text})

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for name, value in HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        return self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status, message):
        return self._json(status, {"error": message})


def serve(port=0, out_dir="."):
    """Run until Ctrl-C. Prints the URL (with the session token) and opens the browser."""
    if not Path(out_dir).is_dir():
        raise SystemExit(f"piggyback: --out {out_dir} is not a directory")
    # SIGTERM/SIGHUP unwind like Ctrl-C so the screenshot folder is removed.
    for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            signal.signal(sig, lambda *_: sys.exit(0))
    # ponytail: the token rides in the opened URL, so other local users can see it in the process
    # list; on a shared machine open a 0600 redirect file instead (as Jupyter does).
    with tempfile.TemporaryDirectory(prefix="piggyback-shots-") as shot_dir:
        screen = Screen(port, out_dir, shot_dir)
        url = f"http://127.0.0.1:{screen.port}/?t={screen.token}"
        print(f"Piggyback is running at {url}\nPress Ctrl-C to stop.", flush=True)
        webbrowser.open(url)
        try:
            screen.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            screen.server_close()
