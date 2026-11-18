"""Frozen C3 acceptance checklist: four checks, run in this order, on one page.

1. render: passes when the body has rendered text (innerText, trimmed, non-empty) AND no
   uncaught pageerror occurred. Console errors (e.g. a 404 image) are recorded and never fail.
2. content: each predicate of the task, read from the DOM after load (see tasks.json).
3. overflow: scrollWidth must not exceed clientWidth at 1440 px, then at 390 px.
4. axe: axe-core, tags wcag2a and wcag2aa, run at 1440 px. Violations fail. Incomplete results
   are recorded and never fail.

Only file paths and loopback http(s) URLs are loaded, and the page may request only loopback
http(s), file, data or blob URLs.

Usage: PYTHONPATH=. python bench/c3/checklist.py PAGE --task T01
"""

import argparse
import contextlib
import ipaddress
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from piggyback import netpolicy
from piggyback.browser import NO_WEBRTC, launch

HERE = Path(__file__).resolve().parent
TASKS_PATH = HERE / "tasks.json"
AXE_PATH = HERE / "vendor" / "axe-core" / "axe.min.js"
AXE_VERSION = "4.14.1"
AXE_TAGS = ["wcag2a", "wcag2aa"]
OVERFLOW_WIDTHS = (1440, 390)
HEIGHT = 900
NAV_MS = 30_000
SETTLE_MS = 5_000
CONTROL_ROLES = (
    "button",
    "link",
    "textbox",
    "combobox",
    "checkbox",
    "radio",
    "switch",
    "tab",
    "menuitem",
)
RENDER_ITEM = "page renders: body has no rendered text, or an uncaught page error occurred"
MESSAGE = (
    "The page failed these checklist items:\n{items}\n"
    "Fix each listed item and return the complete page."
)
SNAPSHOT_JS = """(selectors) => ({
  text: document.body ? document.body.innerText : "",
  counts: Object.fromEntries(selectors.map((s) => [s, document.querySelectorAll(s).length])),
})"""
OVERFLOW_JS = "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
AXE_RUN_JS = """async (tags) => {
  const r = await axe.run(document, { runOnly: { type: "tag", values: tags } });
  const brief = (v) => ({ id: v.id, help: v.help, impact: v.impact, nodes: v.nodes.length });
  return { violations: r.violations.map(brief), incomplete: r.incomplete.map(brief) };
}"""


def is_loopback_host(host):
    # A literal IP only: a name would need DNS, and DNS is a lookup we do not make here.
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def target_url(target):
    if Path(target).is_file():
        return Path(target).resolve().as_uri()
    url = netpolicy.local_name(target)
    parts = urlsplit(url)
    if parts.scheme not in netpolicy.SCHEMES or not is_loopback_host(parts.hostname):
        raise ValueError(f"refusing non-loopback target: {target}")
    return url


def allowed_request(url):
    parts = urlsplit(netpolicy.local_name(url))
    if parts.scheme in ("file", "data", "blob"):
        return True
    return parts.scheme in netpolicy.SCHEMES and is_loopback_host(parts.hostname)


def evaluate_predicate(pred, snap):
    kind = pred["kind"]
    if kind == "text":
        observed = bool(re.search(pred["pattern"], snap["text"], re.IGNORECASE))
        ok = observed
    elif kind == "count":
        observed = snap["counts"][pred["selector"]]
        ok = observed >= pred["min"]
    elif kind == "control_name":
        observed = [n for n in snap["controls"] if re.search(pred["pattern"], n, re.IGNORECASE)]
        ok = bool(observed)
    else:
        raise ValueError(f"unknown predicate kind: {kind}")
    return {"item": pred["item"], "kind": kind, "pass": ok, "observed": observed}


def has_horizontal_overflow(scroll_width, client_width):
    return scroll_width > client_width


def failed_items(checks):
    items = []
    if not checks["render"]["pass"]:
        items.append(RENDER_ITEM)
    items += [f"content item missing: {c['item']}" for c in checks["content"] if not c["pass"]]
    items += [
        f"horizontal overflow at {o['width']} px" for o in checks["overflow"] if not o["pass"]
    ]
    axe = checks["axe"]
    if axe["error"] is not None:
        items.append(f"axe-core could not run: {axe['error']}")
    items += [f"axe-core violation {v['id']}: {v['help']}" for v in axe["violations"]]
    return items


def correction_message(failed):
    if not failed:
        return None
    return MESSAGE.format(items="\n".join(f"- {item}" for item in failed))


def predicates_for(task_id):
    tasks = json.loads(TASKS_PATH.read_text(encoding="utf-8"))["tasks"]
    matches = [t for t in tasks if t["id"] == task_id]
    if len(matches) != 1:
        raise ValueError(f"unknown task: {task_id}")
    return matches[0]["predicates"]


def control_names(page):
    session = page.context.new_cdp_session(page)
    nodes = session.send("Accessibility.getFullAXTree")["nodes"]
    return [
        node["name"]["value"]
        for node in nodes
        if node.get("role", {}).get("value") in CONTROL_ROLES
        and node.get("name", {}).get("value")
        and not node.get("ignored")
    ]


def run_axe(page):
    try:
        page.add_script_tag(path=str(AXE_PATH))
        result = page.evaluate(AXE_RUN_JS, AXE_TAGS)
    except Exception as exc:  # fails closed; keep the first line of the error
        lines = str(exc).strip().splitlines()
        return {
            "version": AXE_VERSION,
            "tags": AXE_TAGS,
            "violations": [],
            "incomplete": [],
            "error": lines[0] if lines else repr(exc),
            "pass": False,
        }
    return {
        "version": AXE_VERSION,
        "tags": AXE_TAGS,
        **result,
        "error": None,
        "pass": not result["violations"],
    }


def run_page_checks(page, predicates, selectors, page_errors, console_errors):
    snap = page.evaluate(SNAPSHOT_JS, selectors)
    snap["controls"] = control_names(page)
    content = [evaluate_predicate(pred, snap) for pred in predicates]
    overflow = []
    for width in OVERFLOW_WIDTHS:
        page.set_viewport_size({"width": width, "height": HEIGHT})
        scroll, client = page.evaluate(OVERFLOW_JS)
        overflow.append(
            {
                "width": width,
                "scroll_width": scroll,
                "client_width": client,
                "pass": not has_horizontal_overflow(scroll, client),
            }
        )
    page.set_viewport_size({"width": 1440, "height": HEIGHT})
    return {
        "render": {
            "pass": bool(snap["text"].strip()) and not page_errors,
            "page_errors": page_errors,
            "console_errors": console_errors,
        },
        "content": content,
        "overflow": overflow,
        "axe": run_axe(page),
    }


def guard_request(route):
    if allowed_request(route.request.url):
        route.continue_()
    else:
        route.abort("blockedbyclient")


def run_checklist(target, task_id):
    url = target_url(target)
    predicates = predicates_for(task_id)
    selectors = sorted({p["selector"] for p in predicates if p["kind"] == "count"})
    # Imported here so this module loads, and its pure checks run, without Playwright.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, label = launch(p)
        try:
            ctx = browser.new_context(
                viewport={"width": 1440, "height": HEIGHT},
                service_workers="block",
                accept_downloads=False,
            )
            ctx.route("**/*", guard_request)
            ctx.add_init_script(NO_WEBRTC)
            page = ctx.new_page()
            page_errors, console_errors = [], []
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
            )
            page.goto(url, wait_until="load", timeout=NAV_MS)
            with contextlib.suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
            checks = run_page_checks(page, predicates, selectors, page_errors, console_errors)
            version = browser.version
        finally:
            browser.close()
    failed = failed_items(checks)
    return {
        "target": url,
        "task": task_id,
        "browser": label,
        "browser_version": version,
        "checks": checks,
        "failed": failed,
        "pass": not failed,
        "correction_message": correction_message(failed),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the frozen C3 checklist on one page.")
    parser.add_argument("target", help="file path or loopback http(s) URL")
    parser.add_argument("--task", required=True, help="task id, T01 to T10")
    args = parser.parse_args(argv)
    report = run_checklist(args.target, args.task)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
