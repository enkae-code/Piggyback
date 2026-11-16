"""`piggyback` command line: check-refs and doctor."""

import argparse
import sys
from pathlib import Path

from . import format as ref_format
from .browser import BrowserUnavailable, launch

SANDBOX_HELP = (
    "Chromium would not start with its sandbox. Install Google Chrome, or run "
    "`python -m playwright install chromium`. On Linux, check unprivileged user "
    "namespaces (AppArmor can block them). Piggyback never falls back to --no-sandbox."
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
            browser = launch(p)
        except BrowserUnavailable as exc:
            print(f"browser: FAILED {exc}")
            print(SANDBOX_HELP)
            return 1
        print(f"browser: starts with sandbox ({browser.browser_type.name} {browser.version})")
        browser.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="piggyback")
    sub = parser.add_subparsers(dest="command", required=True)
    refs = sub.add_parser("check-refs", help="validate spec v1 reference files")
    refs.add_argument("directory")
    sub.add_parser("doctor", help="check Python, Playwright and the sandboxed browser")
    args = parser.parse_args(argv)
    if args.command == "check-refs":
        return check_refs(args.directory)
    return doctor()
