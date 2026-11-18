"""Score a captured C1 run: pins from piggyback.gap, checked against the locked snapshot targets.

Refuses to run unless the run has a manifest, targets.lock.json matches targets.json (targets are
locked from the snapshots before any pin is read), and every receipt was captured with the Chromium
the manifest registered. Writes pins.csv, anchors.csv, pair_summary.csv and verdict.json.

Replacements come only from eligibility.json (capture.plan_retained): each replace decision takes
the next unused reserve, in row order, and a replacement that failed is replaced in turn.
Refuses when verdict.json already exists: the first scoring is final, and a later experiment
needs a new run id.

Usage: python -m bench.c1.score --store STORE --run RUN_ID
"""

import argparse
import csv
import io
import json
import os
import sys

from bench.c1.capture import SIDES, load_pairs, pair_status, plan_retained
from bench.c1.manifest import (
    DEFAULT_STORE,
    SAMPLE_SIZE,
    check_registered,
    load_manifest,
    run_folder,
)
from bench.c1.targets import js_round, sha256_file
from piggyback import fingerprint as fp
from piggyback.gap import ANCHORS, gap

SLOTS = tuple(ANCHORS)
SLOT_OF_CONCEPT = {concept: name for name, (_, concept) in ANCHORS.items()}
PX_TOLERANCE = 1.0  # CSS px after unit conversion
PASS_NUMERATOR, PASS_DENOMINATOR = 9, 10  # 90%, exact integer arithmetic
MIN_AVG_PINNED = 3.0
PAIR_COLUMNS = ["pair_id", "shown_pins", "correct_pins", "anchors_pinned", "receipt_status"]
# One row per numbered pin.
PIN_COLUMNS = [
    "pair_id",
    "slot",
    "pin_number",
    "shown",
    "element_right",
    "pin_correct",
    "expected_target",
    "reviewer",
    "notes",
    "reference_requested_url",
    "reference_final_url",
    "reference_receipt",
    "build_requested_url",
    "build_final_url",
    "build_receipt",
    "reference_shown_box",
    "build_shown_box",
]
# One row per pin and side and measured property.
PROP_COLUMNS = [
    "pair_id",
    "slot",
    "pin_number",
    "side",
    "property",
    "piggyback_value",
    "hand_value",
    "unit",
    "abs_diff",
    "tolerance",
    "verdict",
]
ANCHOR_COLUMNS = ["pair_id", "slot", "disposition", "pin_number"]


def _fully_in(box, vw, vh):
    return (
        box["x"] >= 0
        and box["y"] >= 0
        and box["x"] + box["width"] <= vw
        and box["y"] + box["height"] <= vh
    )


def placement(box, viewport):
    """The overlay rule of piggyback/web/app.js placement(): in, below, outside, partly or none."""
    if not box or not viewport:
        return "none"
    vw, vh = viewport["width"], viewport["height"]
    if _fully_in(box, vw, vh):
        return "in"
    if box["y"] >= vh:
        return "below"
    if box["y"] + box["height"] <= 0 or box["x"] + box["width"] <= 0 or box["x"] >= vw:
        return "outside"
    return "partly"


def box_matches(pin_box, target_box):
    """Shown box vs locked target box: exactly equal after JavaScript Math.round, value by value."""
    if target_box is None or pin_box is None:
        return False
    got = [js_round(pin_box[k]) for k in ("x", "y", "width", "height")]
    return got == list(target_box)


def within(kind, expected, actual):
    """Tolerance: zero must be zero; px within 1; percent within 2 points; else 2% relative."""
    if kind == "string":
        return " ".join(str(expected).lower().split()) == " ".join(str(actual).lower().split())
    if not isinstance(expected, float) or not isinstance(actual, float):
        return False
    if expected == 0:
        return actual == 0
    if kind == "px":
        return abs(actual - expected) <= PX_TOLERANCE
    if kind == "pct":
        return abs(actual - expected) <= 2.0
    return abs(actual - expected) <= 0.02 * abs(expected)


class HarnessDefect(RuntimeError):
    """A scored value has no locked hand value: the harness is wrong, not Piggyback."""


# gap's property name -> the locked computed-style key it is checked against
HAND_KEYS = {
    "font_size": "font-size",
    "line_height": "line-height",
    "letter_spacing": "letter-spacing",
}


def reported_props(detail):
    """Properties a pin's detail reports: size and line, plus tracking when shown."""
    props = ["font_size", "line_height"]
    if ", tracking " in detail:
        props.append("letter_spacing")
    return props


def _shown(value):
    """A size or line as fingerprint.fmt shows it: one decimal place at most."""
    return value if value is None or isinstance(value, str) else float(fp.fmt(value))


def _tracking(letter, own_size, font):
    """Tracking in px as the spec rounds it: em to two places from own values, at font."""
    return round(letter / own_size, 2) * font


def _hand_value(prop, raw, font):
    if prop == "font_size":
        value = _shown(font)
    elif prop == "line_height":
        value = raw if raw == "normal" else _shown(fp.px(raw))
    else:  # tracking is locked in px; both sides convert through the hand font size
        tracking = 0.0 if raw == "normal" else fp.px(raw)
        value = _tracking(tracking, font, font) if tracking is not None and font else None
    if value is None:
        raise HarnessDefect(f"no locked hand value for {HAND_KEYS[prop]}: {raw!r}")
    return value


def hand_values(target, props):
    """Hand-checked values for the named properties, from the locked snapshot style."""
    style = target["style"]
    font = fp.px(style.get(HAND_KEYS["font_size"]))
    return {prop: _hand_value(prop, style.get(HAND_KEYS[prop]), font) for prop in props}


def piggyback_values(anchor, font):
    """Piggyback's displayed type values for one side. Tracking converts through the hand font."""
    size = fp.px(anchor.get("font_size"))
    line = anchor.get("line_height")
    letter = (
        0.0 if anchor.get("letter_spacing") == "normal" else fp.px(anchor.get("letter_spacing"))
    )
    tracking = None
    if letter is not None and size and font:
        tracking = _tracking(letter, size, font)
    return {
        "font_size": _shown(size),
        "line_height": line if line == "normal" else _shown(fp.px(line)),
        "letter_spacing": tracking,
    }


def _box_text(box):
    if box is None:
        return ""
    return f"{box['x']},{box['y']},{box['width']},{box['height']}"


def _anchor_pins(pins):
    """Numbered pins for the five anchor slots, keyed by slot. Page-level pins are left out."""
    return {
        SLOT_OF_CONCEPT[pin.concept]: pin
        for pin in pins
        if pin.number is not None and pin.concept in SLOT_OF_CONCEPT
    }


def _right(box, target):
    """A shown box is right when the side has a locked target and the box matches it exactly."""
    return not target.get("absent") and box_matches(box, target["box"])


def _audit(meta, pin):
    """Audit cells: both sides' requested and final URL and receipt, then both shown boxes."""
    cells = []
    for side in SIDES:
        cells += [meta[side]["requested_url"], meta[side]["final_url"], meta[side]["receipt"]]
    return cells + [_box_text(pin.ref_box), _box_text(pin.build_box)]


def _side_values(target, anchor, props):
    """(hand, mine) per property for one side. hand is None when the target is absent."""
    absent = bool(target.get("absent"))
    hand = {p: None for p in props} if absent else hand_values(target, props)
    font = None if absent else fp.px(target["style"].get("font-size"))
    return hand, piggyback_values(anchor, font)


def _expected(target):
    return "absent" if target.get("absent") else f"{target['tag']} {target['text']!r}"


def _value_rows(pair_id, slot, pin, target, anchors):
    """One row per side and property: hand-checked value against Piggyback's own value."""
    rows, value_ok = [], True
    props = reported_props(pin.detail)
    for side in SIDES:
        hand, mine = _side_values(target[side], anchors[side], props)
        for prop in props:
            ok = within("string" if isinstance(hand[prop], str) else "px", hand[prop], mine[prop])
            numeric = isinstance(hand[prop], float) and isinstance(mine[prop], float)
            rows.append(
                [
                    pair_id,
                    slot,
                    pin.number,
                    side,
                    prop,
                    mine[prop],
                    hand[prop],
                    "px",
                    abs(mine[prop] - hand[prop]) if numeric else None,
                    PX_TOLERANCE,
                    "correct" if ok else "incorrect",
                ]
            )
            value_ok = value_ok and ok
    return rows, value_ok


def _disposition(spots):
    """Anchor disposition: pinned only when both sides show a fully visible overlay."""
    if all(spot == "in" for spot in spots.values()):
        return "pinned"
    return "partly visible" if "partly" in spots.values() else "no pin"


def _slot_rows(pair_id, slot, pin, target, anchors, fps, meta):
    """Anchor disposition, the pin row, and its property rows for one slot.

    Returns (anchor_row, pin_row, prop_rows, counts).
    """
    boxes = {"reference": pin.ref_box, "build": pin.build_box}
    spots = {side: placement(boxes[side], fps[side].get("viewport")) for side in SIDES}
    shown_sides = [side for side in SIDES if spots[side] == "in"]
    disposition = _disposition(spots)
    shown = bool(shown_sides)
    element_right = shown and all(_right(boxes[s], target[s]) for s in shown_sides)
    prop_rows, value_ok = _value_rows(pair_id, slot, pin, target, anchors)
    correct = shown and element_right and value_ok
    expected = f"reference: {_expected(target['reference'])}; build: {_expected(target['build'])}"
    pin_row = [
        pair_id,
        slot,
        pin.number,
        shown,
        element_right,
        correct,
        expected,
        "bot",
        None,
        *_audit(meta, pin),
    ]
    counts = (int(shown), int(correct), int(disposition == "pinned"))
    return [pair_id, slot, disposition, pin.number], pin_row, prop_rows, counts


def score_pair(pair_id, targets, fps, meta):
    """Rows for one retained pair.

    Returns (pin_rows, prop_rows, anchor_rows, shown, correct, pinned, skipped).
    skipped counts numbered page-level pins (accent, container, motion): outside pin accuracy.
    A slot with no numbered pin is 'not found' when Piggyback has no anchor for it on a side,
    else 'no pin'. A shown pin whose target is absent on its side is scored incorrect.
    """
    pins = gap(fps["reference"], fps["build"])
    by_slot = _anchor_pins(pins)
    skipped = sum(
        1 for pin in pins if pin.number is not None and pin.concept not in SLOT_OF_CONCEPT
    )
    pin_rows, prop_rows, anchor_rows = [], [], []
    shown_total = correct_total = pinned_total = 0
    for slot in SLOTS:
        target = {side: targets[side][slot] for side in SIDES}
        anchors = {side: fp.anchor(fps[side], slot) for side in SIDES}
        pin = by_slot.get(slot)
        if pin is None:
            missing = any(anchor is None for anchor in anchors.values())
            anchor_rows.append([pair_id, slot, "not found" if missing else "no pin", ""])
            continue
        anchor_row, pin_row, props, (shown, correct, pinned) = _slot_rows(
            pair_id, slot, pin, target, anchors, fps, meta
        )
        anchor_rows.append(anchor_row)
        pin_rows.append(pin_row)
        prop_rows.extend(props)
        shown_total += shown
        correct_total += correct
        pinned_total += pinned
    return (
        pin_rows,
        prop_rows,
        anchor_rows,
        shown_total,
        correct_total,
        pinned_total,
        skipped,
    )


def _private_write(path, text):
    # ponytail: no O_NOFOLLOW on Windows, so refuse a link by lstat first; a race is possible
    # there, the run folder is single-user. Upgrade path: a directory fd where O_NOFOLLOW exists.
    if not hasattr(os, "O_NOFOLLOW") and os.path.islink(path):
        raise SystemExit("refusing: output path is a symbolic link")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _csv(columns, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue()


def _lock_ok(run):
    lock_path = run / "targets.lock.json"
    if not lock_path.is_file() or lock_path.is_symlink():
        raise SystemExit("refusing: targets are not locked (run bench.c1.targets first)")
    record = json.loads(lock_path.read_text(encoding="utf-8"))
    if sha256_file(run / "targets.json") != record["targets_sha256"]:
        raise SystemExit("refusing: targets.json changed after it was locked")
    return json.loads((run / "targets.json").read_text(encoding="utf-8"))


def _load_pair(run, pid, targets, chromium):
    """(fingerprints, audit meta) for an ok pair with locked targets, else None."""
    pair_file = run / pid / "pair.json"
    if not pair_file.is_file() or pid not in targets:
        return None
    pair = json.loads(pair_file.read_text(encoding="utf-8"))
    if pair["status"] not in ("ok", "suspected_challenge"):
        return None
    fps, meta = {}, {}
    for side in SIDES:
        entry = pair["sides"][side]
        receipt = f"{pid}/{side}/attempt-{entry['attempt']}"
        fingerprint = run / pid / side / f"attempt-{entry['attempt']}" / "fingerprint.json"
        fps[side] = json.loads(fingerprint.read_text(encoding="utf-8"))
        capture = fps[side]["capture"]
        if capture["browser_version"] != chromium:
            raise SystemExit(
                f"refusing: {receipt} was captured with Chromium {capture['browser_version']}, "
                f"manifest registered {chromium}"
            )
        meta[side] = {
            "requested_url": entry["requested_url"],
            "final_url": capture["final_url"],
            "receipt": receipt,
        }
    return fps, meta


def required_correct(shown):
    """Fewest correct pins that pass: 90% of the shown pins, rounded up to a whole pin."""
    return -(-PASS_NUMERATOR * shown // PASS_DENOMINATOR)


def _verdict(scored, primary_count, unresolved, shown, correct, pinned):
    """pass or fail on the thresholds; incomplete when any pair is unresolved or missing."""
    required = required_correct(shown)
    accuracy_ok = shown > 0 and correct >= required
    anchors_ok = scored == primary_count and pinned >= MIN_AVG_PINNED * scored
    if unresolved or scored != primary_count:
        return "incomplete", required
    return ("pass" if accuracy_ok and anchors_ok else "fail"), required


def _unresolved(rows, pid, status):
    rows["unresolved"].append(pid)
    rows["pairs"].append([pid, 0, 0, 0, status])
    rows["anchors"] += [[pid, slot, "not found", ""] for slot in SLOTS]


def _score_plan(run, plan, targets, chromium):
    """Rows for every chain in row order, and the (shown, correct, pinned, skipped) totals."""
    rows = {"unresolved": [], "pins": [], "props": [], "anchors": [], "pairs": []}
    totals = [0, 0, 0, 0]
    for chain in plan:
        for before, after in zip(chain, chain[1:], strict=False):
            if after is not None:
                rows["pairs"].append([before, 0, 0, 0, f"replaced by {after}"])
        scored = chain[-1]
        if scored is None:
            _unresolved(rows, chain[-2], "no reserve left")  # the last pair that needed a reserve
            continue
        loaded = _load_pair(run, scored, targets, chromium)
        if loaded is None:
            _unresolved(rows, scored, pair_status(run, scored) or "missing")
            continue
        pr, prop, ar, *counts = score_pair(scored, targets[scored], *loaded)
        rows["pins"] += pr
        rows["props"] += prop
        rows["anchors"] += ar
        rows["pairs"].append([scored, *counts[:3], pair_status(run, scored)])
        totals = [a + b for a, b in zip(totals, counts, strict=True)]
    return rows, totals


def _report(manifest, data, plan, rows, totals):
    shown, correct, pinned, skipped = totals
    unresolved = sorted(set(rows["unresolved"]))
    scored = len(plan) - len(rows["unresolved"])  # resolved pairs only
    verdict, required = _verdict(scored, len(data["primary"]), unresolved, shown, correct, pinned)
    sample = manifest["spot_check"]["sample"]
    return {
        "verdict": verdict,
        "shown_pins": shown,
        "correct_pins": correct,
        "required_correct": required,
        "anchors_pinned": pinned,
        "page_level_pins_skipped": skipped,
        "retained_pairs": scored,
        "avg_pinned_anchors": round(pinned / scored, 2) if scored else None,
        "unresolved": unresolved,
        "replacements": [f"{c[0]}={c[-1]}" for c in plan if len(c) > 1 and c[-1] is not None],
        "spot_check": {
            "seed": manifest["spot_check"]["seed"],
            "sample_size": SAMPLE_SIZE,
            "sample": sample,
            "retained_for_sample": {c[0]: c[-1] for c in plan if c[0] in sample},
        },
        "manifest_commit": manifest["piggyback_commit"],
        "chromium_version": manifest["chromium_version"],
    }


def run_score(store, run_id):
    run = run_folder(store, run_id)
    if not run.is_dir():
        raise SystemExit(f"refusing: no run {run_id} in {store}")
    manifest = load_manifest(run)
    check_registered(manifest)
    verdict_path = run / "verdict.json"
    if verdict_path.exists() or verdict_path.is_symlink():
        raise SystemExit(f"refusing: {verdict_path} exists; the first scoring is final")
    targets = _lock_ok(run)
    data = load_pairs()
    plan = plan_retained(run, data)  # decisions are checked before any pin is read or written
    rows, totals = _score_plan(run, plan, targets, manifest["chromium_version"])
    report = _report(manifest, data, plan, rows, totals)
    _private_write(run / "pins.csv", _csv(PIN_COLUMNS, rows["pins"]))
    _private_write(run / "pin_properties.csv", _csv(PROP_COLUMNS, rows["props"]))
    _private_write(run / "anchors.csv", _csv(ANCHOR_COLUMNS, rows["anchors"]))
    _private_write(run / "pair_summary.csv", _csv(PAIR_COLUMNS, rows["pairs"]))
    _private_write(verdict_path, json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run_score(args.store, args.run), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
