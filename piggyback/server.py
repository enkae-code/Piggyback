"""`piggyback` local screen: one page on 127.0.0.1 that measures a reference and an optional build.

Stdlib only. Every API call carries the per-launch token; Host and Origin must name this server.
Results live in memory for the session. Screenshots are served by opaque id from a temp dir.
"""

import json
import re
import secrets
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import brief as brief_mod
from . import defaults as defaults_mod
from . import gap as gap_mod
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
    "frame-ancestors 'none'"
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


def _plain(text):
    return ESCAPED.sub(r"\1", text)


def _loopback(url):
    """The engine refuses the name `localhost` even with loopback allowed; the build slot means it
    as 127.0.0.1. Only the exact name is rewritten, so the port and path are kept."""
    parts = urlsplit(url)
    if parts.hostname != "localhost":
        return url
    port = f":{parts.port}" if parts.port else ""
    return parts._replace(netloc=f"127.0.0.1{port}").geturl()


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
    build = _loopback(build) if build else None
    # Reference: public addresses only. Build: loopback too (your own localhost), still no LAN.
    for url in refs:
        check_url(url)
    if build:
        check_url(build, allow_loopback=True)
    return refs, build


def _line(concept, detail, seen):
    """One glossary line. The why-it-matters sentence is shown once per concept on the page."""
    name, reason = TERMS[concept]
    why = None if concept in seen else reason
    seen.add(concept)
    return {"concept": concept, "name": name, "detail": _plain(detail), "why": why}


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


def _compare(fps, build_fp, seen):
    pins = gap_mod.gap(fps[0], build_fp)
    views = [
        {
            **_line(pin.concept, pin.detail, seen),
            "number": pin.number,
            "ref_box": pin.ref_box,
            "build_box": pin.build_box,
        }
        for pin in pins
        if pin.number is not None
    ]
    payload = {
        "state": "compare",
        "reference": _view(fps[0]),
        "build": _view(build_fp),
        "pins": views,
        "not_found": [_plain(pin.detail) for pin in pins if pin.number is None],
        "note": "Differences are against the first reference only." if len(fps) > 1 else "",
    }
    return payload, brief_mod.brief(fps) + "\n" + gap_mod.render(pins)


def _render(fps, build_fp):
    """(state payload for the page, BRIEF.md text) for one run."""
    seen = set()
    if build_fp is None:
        return _brief(fps, seen)
    if not fps:
        return _defaults(build_fp, seen)
    return _compare(fps, build_fp, seen)


class Screen(ThreadingHTTPServer):
    def __init__(self, port, out_dir, shot_dir):
        super().__init__(("127.0.0.1", port), Handler)
        self.port = self.server_address[1]
        self.token = secrets.token_urlsafe(24)
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.out_dir = Path(out_dir).resolve()
        self.shot_dir = Path(shot_dir)
        self.busy = threading.Lock()  # one measure batch at a time
        self.shots = {}  # opaque id -> screenshot path, last run only
        self.last_text = None  # BRIEF.md text of the last run

    def run(self, refs, build):
        shots = {}

        def shoot(url, allow_loopback):
            shot_id = secrets.token_hex(16)
            path = self.shot_dir / f"{shot_id}.png"
            fp = measure(url, allow_loopback=allow_loopback, screenshot=path)
            shots[shot_id] = path
            return {**fp, "shot": shot_id}

        fps = [shoot(url, False) for url in refs]
        build_fp = shoot(build, True) if build else None
        payload, text = _render(fps, build_fp)
        for old in self.shots.values():
            old.unlink(missing_ok=True)
        self.shots, self.last_text = shots, text
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
        try:
            result = self.server.run(refs, build)
        except MeasureError as exc:
            return self._error(422, str(exc))
        finally:
            self.server.busy.release()
        return self._json(200, result)

    def _brief(self):
        text = self.server.last_text
        if text is None:
            return self._error(409, "measure first, then copy the brief")
        path = self.server.out_dir / "BRIEF.md"
        try:
            path.write_text(text, encoding="utf-8")
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
