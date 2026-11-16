"""`piggyback` command line: measure, check-refs and doctor."""

import argparse
import json
import re
import sys
from pathlib import Path

from . import format as ref_format
from .browser import BrowserUnavailable, launch
from .measure import MeasureError, measure

SANDBOX_HELP = (
    "Chromium would not start with its sandbox. Install Google Chrome, or run "
    "`python -m playwright install chromium`. On Linux, check unprivileged user "
    "namespaces (AppArmor can block them). Piggyback never falls back to --no-sandbox.\n"
    "Ubuntu 23.10+: install Google Chrome (its package ships an AppArmor profile), or add an "
    "AppArmor profile that allows unprivileged user namespaces for Playwright's chromium."
)


def check_refs(directory):
    root = Path(directory)
    refs = root / "refs" if (root / "refs").is_dir() else root
    failures = 0
    paths = sorted(refs.glob("*.md"))
    for path in paths:
        try:
            meta, *_ = ref_format.parse_ref(path.read_text(encoding="utf-8"))
            problems = ref_format.validate(meta)
        except (ValueError, UnicodeDecodeError) as exc:
            problems = [str(exc)]
        for problem in problems:
            print(f"{path}: {problem}")
        failures += bool(problems)
    print(f"{len(paths)} refs checked, {failures} with problems")
    return 1 if failures else 0


def viewport_arg(value):
    match = re.fullmatch(r"(\d{3,5})x(\d{3,5})", value)
    if not match:
        raise argparse.ArgumentTypeError("viewport must look like 1440x900")
    return int(match[1]), int(match[2])


def run_measure(url, out, viewport, allow_loopback):
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    shot = out_dir / "screenshot.png"
    try:
        result = measure(url, viewport=viewport, allow_loopback=allow_loopback, screenshot=shot)
    except MeasureError as exc:
        print(f"measure failed: {exc}", file=sys.stderr)
        return 1
    fingerprint = out_dir / "fingerprint.json"
    fingerprint.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {fingerprint} and {shot}")
    return 0


def doctor():
    print(
        f"python: {sys.version.split()[0]}",
        "(ok)" if sys.version_info >= (3, 11) else "(need 3.11+)",
    )
    if sys.version_info < (3, 11):
        return 1
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright: not installed; run: pip install 'piggyback-design[measure]'")
        return 1
    print("playwright: installed")
    with sync_playwright() as p:
        try:
            browser, label = launch(p)
        except BrowserUnavailable as exc:
            print(f"browser: FAILED {exc}")
            print(SANDBOX_HELP)
            return 1
        print(
            f"browser: starts with sandbox ({label}, {browser.browser_type.name} {browser.version})"
        )
        browser.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="piggyback")
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("measure", help="fingerprint a page into fingerprint.json + screenshot.png")
    m.add_argument("url")
    m.add_argument("--out", default=".", help="output directory (default: current directory)")
    m.add_argument("--viewport", type=viewport_arg, default=(1440, 900), help="WIDTHxHEIGHT")
    m.add_argument("--allow-loopback", action="store_true", help="allow 127.0.0.0/8 and ::1 only")
    refs = sub.add_parser("check-refs", help="validate spec v1 reference files")
    refs.add_argument("directory")
    sub.add_parser("doctor", help="check Python, Playwright and the sandboxed browser")
    args = parser.parse_args(argv)
    if args.command == "measure":
        return run_measure(args.url, args.out, args.viewport, args.allow_loopback)
    if args.command == "check-refs":
        return check_refs(args.directory)
    return doctor()
