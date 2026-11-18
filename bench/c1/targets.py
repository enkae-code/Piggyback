"""Lock the expected anchor targets of each side from its saved DOM snapshot, before any scoring.

Reads only evidence.json (DOMSnapshot, computed styles, rects, text). Never reads a Piggyback
fingerprint, so the targets cannot follow Piggyback's choices. Rules follow the C1 spec ("Scoring")
and measure.js: a candidate is rendered when display is not none, visibility is not hidden,
opacity is not 0, it has nonzero geometry, and it intersects the viewport.

Usage: python -m bench.c1.targets --store STORE --run RUN_ID
"""

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from bench.c1.snapshot import Snapshot, px

SLOTS = ("heading", "body", "cta", "nav", "card")
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


def _on_screen(box, vw, vh):
    x, y, w, h = box
    return w > 0 and h > 0 and x < vw and x + w > 0 and y < vh and y + h > 0


def visible(el, vw, vh):
    """Rendered for the captured checks: display, visibility, opacity, geometry, viewport."""
    if el.style is None or el.box is None or _hidden(el.style):
        return False
    return _on_screen(el.box, vw, vh)


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


def ink_box(snap, el):
    """Bounding box of the element's text, clipped to the element; the element box if no text."""
    x, y, w, h = el.box
    if not el.boxes:
        return el.box
    left = min(b[0] for b in el.boxes)
    top = min(b[1] for b in el.boxes)
    right = max(b[0] + b[2] for b in el.boxes)
    bottom = max(b[1] + b[3] for b in el.boxes)
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


def _target(snap, el, box, style_keys):
    values = {k: el.style.get(k) for k in style_keys}
    return {
        "tag": el.tag,
        "dom_index": el.idx,
        "text": snap.inner_text(el.idx)[:80],
        "box": [js_round(v) for v in box],
        "style": values,
    }


def _text_slot(snap, el, box):
    return _target(snap, el, box, ("font-size", "line-height", "letter-spacing"))


def _heading(snap, els, vw, vh):
    pool = [el for el in els if el.tag in HEADINGS and snap.inner_text(el.idx) != ""]
    heading = pick_first_best(
        pool, lambda el: (px(el.style["font-size"]) or 0, clipped_area(el, vw, vh))
    )
    return _text_slot(snap, heading, ink_box(snap, heading)) if heading else None


def _body(snap, els):
    body = next((el for el in els if el.tag == "p" and len(snap.inner_text(el.idx)) >= 40), None)
    return _text_slot(snap, body, ink_box(snap, body)) if body else None


def _nav(snap, els):
    nav = next((el for el in els if el.tag == "nav" or el.attrs.get("role") == "navigation"), None)
    return _target(snap, nav, nav.box, ("font-size",)) if nav else None


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
        "card": _card(snap, els, vw),
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
    return _target(snap, best, best.box, ("font-size",)) if best else None


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
    """Rendered elements sharing a parent, tag and class, in DOM order per group."""
    groups = {}
    for el in els:
        cls = el.attrs.get("class")
        if cls is None or el.parent < 0:
            continue
        groups.setdefault((el.parent, el.tag, cls), []).append(el)
    return groups.values()


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
    target = _target(snap, first, first.box, ())
    target["count"] = len(best)
    return target


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def lock(store, run_id):
    """Write targets.json and its lock record once per run. Refuses to overwrite a lock."""
    run = Path(store) / run_id
    lock_path = run / "targets.lock.json"
    if lock_path.exists() or lock_path.is_symlink():
        raise SystemExit(f"targets already locked: {lock_path}")
    pairs = {}
    for pair_dir in sorted(p for p in run.iterdir() if p.is_dir()):
        sides = {}
        for side in ("reference", "build"):
            evidence = pair_dir / side / "evidence.json"
            if evidence.exists():
                data = json.loads(evidence.read_text(encoding="utf-8"))
                sides[side] = lock_targets(data)
        if sides:
            pairs[pair_dir.name] = sides
    targets_path = run / "targets.json"
    targets_path.write_text(json.dumps(pairs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    targets_path.chmod(0o600)
    record = {
        "locked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "targets_sha256": sha256_file(targets_path),
        "pairs": sorted(pairs),
        "source": "evidence.json snapshots only; no fingerprint read",
    }
    lock_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
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
