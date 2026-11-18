"""Lock the expected anchor targets of each side from its saved DOM snapshot, before any scoring.

Reads only evidence.json (DOMSnapshot, computed styles, rects, text). Never reads a Piggyback
fingerprint, so the targets cannot follow Piggyback's choices. Rules follow the C1 spec ("Scoring")
and measure.js: a candidate is rendered when display is not none, visibility is not hidden,
opacity is not 0, it has nonzero geometry, and it intersects the viewport.

Usage: python -m bench.c1.targets --store STORE --run RUN_ID
"""

import argparse
import json
import math
import re
from datetime import datetime, timezone

from bench.c1.capture import SIDES, load_pairs, plan_retained
from bench.c1.manifest import check_registered, load_manifest, run_folder, sha256_file
from bench.c1.snapshot import Snapshot, px
from piggyback.measure import publish_json

SLOTS = ("heading", "body", "cta", "nav", "card")
# Every computed property piggyback.gap can report for an anchor (font size, line height, tracking).
TYPE_KEYS = ("font-size", "line-height", "letter-spacing")
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
CONTROL_TAGS = {"button", "input"}
RGBA = re.compile(r"rgba?\(([^)]*)\)")


def js_round(value):
    """JavaScript Math.round: halves round up."""
    return math.floor(value + 0.5)


def _hidden(style):
    return (
        style.get("display") == "none"
        or style.get("visibility") == "hidden"
        or style.get("opacity") == "0"
    )


def rendered(el):
    """Rendered for the captured checks: display, visibility, opacity and nonzero geometry."""
    if el.style is None or el.box is None or _hidden(el.style):
        return False
    return el.box[2] > 0 and el.box[3] > 0


def visible(el, vw, vh):
    """Rendered and intersecting the viewport. Cards use rendered() alone: they keep below-fold."""
    if not rendered(el):
        return False
    x, y, w, h = el.box
    return x < vw and x + w > 0 and y < vh and y + h > 0


def clipped_area(el, vw, vh):
    x, y, w, h = el.box
    width = min(x + w, vw) - max(x, 0)
    height = min(y + h, vh) - max(y, 0)
    return width * height if width > 0 and height > 0 else 0.0


def has_background(el):
    """A nontransparent computed background: rgba alpha above 0 (or an rgb() with no alpha)."""
    value = (el.style or {}).get("background-color", "")
    found = RGBA.fullmatch(value.strip()) if value else None
    if not found:
        return False
    parts = [p for p in re.split(r"[\s,/]+", found.group(1)) if p]
    return len(parts) == 3 or float(parts[3].rstrip("%")) > 0


def _descendant_boxes(snap, el):
    for child in el.children:
        if child in snap.els:
            yield snap.els[child].box
            yield from _descendant_boxes(snap, snap.els[child])


def ink_box(snap, el):
    """measure.js ink rect: Range.getBoundingClientRect over the element's contents, clipped to it.

    The range covers the text boxes under el (already attached to every ancestor) and the border
    boxes of every descendant element, so a block child widens the ink. Falls back to the element
    box when nothing is left.
    """
    x, y, w, h = el.box
    boxes = el.boxes + [b for b in _descendant_boxes(snap, el) if b]
    if not boxes:
        return el.box
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    right = max(b[0] + b[2] for b in boxes)
    bottom = max(b[1] + b[3] for b in boxes)
    left, top = max(left, x), max(top, y)
    right, bottom = min(right, x + w), min(bottom, y + h)
    return (left, top, right - left, bottom - top) if right > left and bottom > top else el.box


def is_control(el):
    return el.tag in CONTROL_TAGS or el.attrs.get("role") == "button"


def in_svg(snap, el):
    while el is not None:
        if el.tag == "svg":
            return True
        el = snap.els.get(el.parent)
    return False


def pick_first_best(candidates, key):
    """Highest key, earliest in DOM order on a tie. Input is in DOM order."""
    best = None
    for el in candidates:
        if best is None or key(el) > key(best):
            best = el
    return best


def _target(snap, el, box):
    """Locked target for one element: its box, text and the type values of every slot."""
    return {
        "tag": el.tag,
        "dom_index": el.idx,
        "text": snap.inner_text(el.idx)[:80],
        "box": [js_round(v) for v in box],
        "style": {k: el.style.get(k) for k in TYPE_KEYS},
    }


def _heading(snap, els, vw, vh):
    pool = [el for el in els if el.tag in HEADINGS and snap.inner_text(el.idx) != ""]
    heading = pick_first_best(
        pool, lambda el: (px(el.style["font-size"]) or 0, clipped_area(el, vw, vh))
    )
    return _target(snap, heading, ink_box(snap, heading)) if heading else None


def _body(snap, els):
    body = next(
        (el for el in els if el.tag == "p" and len(snap.paragraph_inner_text(el.idx)) >= 40),
        None,
    )
    return _target(snap, body, ink_box(snap, body)) if body else None


def _nav(snap, els):
    nav = next((el for el in els if el.tag == "nav" or el.attrs.get("role") == "navigation"), None)
    return _target(snap, nav, nav.box) if nav else None


def lock_targets(evidence):
    """Expected target per slot for one side: a dict, or {"absent": True} when none qualifies."""
    snap = Snapshot(evidence)
    vw, vh = snap.viewport
    els = [el for el in snap.elements() if visible(el, vw, vh)]
    targets = {
        "heading": _heading(snap, els, vw, vh),
        "body": _body(snap, els),
        "cta": _cta(snap, els, vw, vh),
        "nav": _nav(snap, els),
        "card": _card(snap, [el for el in snap.elements() if rendered(el)], vw),
    }
    return {slot: targets[slot] or {"absent": True} for slot in SLOTS}


def _is_cta_candidate(el):
    if el.tag == "button":
        return True
    if el.tag == "input":
        return el.attrs.get("type") in ("button", "submit")
    if el.tag != "a":
        return False
    classes = el.attrs.get("class", "").lower()
    return el.attrs.get("role") == "button" or "btn" in classes or "button" in classes


def _cta(snap, els, vw, vh):
    cands = [el for el in els if _is_cta_candidate(el)]
    painted = [el for el in cands if has_background(el)]
    pool = painted or cands
    best = pick_first_best(pool, lambda el: clipped_area(el, vw, vh))
    return _target(snap, best, best.box) if best else None


def _qualifies_as_card(snap, el, vw):
    x, y, w, h = el.box
    has_content = snap.inner_text(el.idx) != "" or any(c in snap.els for c in el.children)
    return (
        w >= 120
        and h >= 80
        and x >= 0
        and x + w <= vw
        and has_content
        and not is_control(el)
        and not in_svg(snap, el)
    )


def _sibling_groups(els):
    """Rendered elements sharing a parent, tag and class, in DOM order per group.

    Walked as measure.js builds them: parents in first-seen order, then signatures within each
    parent in first-seen order, so a tie goes to the same group.
    """
    by_parent = {}
    for el in els:
        cls = el.attrs.get("class")
        if not cls or el.parent < 0:  # an empty class is no class, as in measure.js
            continue
        by_parent.setdefault(el.parent, {}).setdefault((el.tag, cls), []).append(el)
    return [group for sigs in by_parent.values() for group in sigs.values()]


def _largest_card_group(snap, els, vw):
    """Qualifying members of the largest group of three or more; the first group wins a tie."""
    best = None
    for members in _sibling_groups(els):
        qualifying = [el for el in members if _qualifies_as_card(snap, el, vw)]
        if len(qualifying) >= 3 and (best is None or len(qualifying) > len(best)):
            best = qualifying
    return best


def _card(snap, els, vw):
    best = _largest_card_group(snap, els, vw)
    if best is None:
        return None
    first = best[0]
    target = _target(snap, first, first.box)
    target["count"] = len(best)
    return target


def lock(store, run_id):
    """Write targets.json and its lock record once per run. Refuses to overwrite a lock.

    Locks the retained set only (kept primaries and chosen reserves). Refuses before any write,
    so an early call cannot burn the one lock.
    """
    run = run_folder(store, run_id)
    manifest = load_manifest(run)
    lock_path = run / "targets.lock.json"
    if lock_path.exists() or lock_path.is_symlink():
        raise SystemExit(f"targets already locked: {lock_path}")
    plan = plan_retained(run, load_pairs())
    check_registered(manifest)
    pairs = {}
    for _, pid in plan:
        if pid is None:
            continue  # no reserve left: unresolved, nothing to lock
        pair = json.loads((run / pid / "pair.json").read_text(encoding="utf-8"))
        if pair["status"] not in ("ok", "suspected_challenge"):
            continue  # a failed pair has no snapshot to lock; it is reported unresolved
        pairs[pid] = {}
        for side in SIDES:
            attempt = pair["sides"][side]["attempt"]
            evidence = run / pid / side / f"attempt-{attempt}" / "evidence.json"
            data = json.loads(evidence.read_text(encoding="utf-8"))
            pairs[pid][side] = lock_targets(data)
    targets_path = run / "targets.json"
    publish_json(targets_path, pairs)  # replaces a link at the path, never writes through it
    targets_path.chmod(0o600)
    record = {
        "locked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "targets_sha256": sha256_file(targets_path),
        "pairs": sorted(pairs),
        "source": "evidence.json snapshots only; no fingerprint read",
    }
    publish_json(lock_path, record)
    lock_path.chmod(0o600)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(lock(args.store, args.run), indent=2))


if __name__ == "__main__":
    main()
