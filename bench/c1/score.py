"""Score a captured C1 run: pins from piggyback.gap, checked against the locked snapshot targets.

Refuses to run unless targets.lock.json exists and its sha256 matches targets.json (targets are
locked from the snapshots before any pin is read). Writes pins.csv, anchors.csv, pair_summary.csv
and verdict.json into the run folder.

Usage: python -m bench.c1.score --store STORE --run RUN_ID [--replace C1-05=R1 ...]
"""

import argparse
import csv
import io
import json
import os
import sys
from pathlib import Path

from bench.c1.capture import DEFAULT_STORE, load_pairs
from bench.c1.targets import js_round, sha256_file
from piggyback import fingerprint as fp
from piggyback.gap import ANCHORS, gap

SLOTS = tuple(ANCHORS)
SLOT_OF_CONCEPT = {concept: name for name, (_, concept) in ANCHORS.items()}
PIN_SLOT_TOLERANCE = 2  # px: a shown box matches the locked target when each value is within 2px
PX_TOLERANCE = 1.0  # CSS px after unit conversion
PASS_NUMERATOR, PASS_DENOMINATOR = 9, 10  # 90%, exact integer arithmetic
MIN_AVG_PINNED = 3.0
PAIR_COLUMNS = ["pair_id", "shown_pins", "correct_pins", "anchors_pinned", "receipt_status"]
PIN_COLUMNS = [
    "pair_id",
    "slot",
    "pin_number",
    "side",
    "shown",
    "element_right",
    "property",
    "piggyback_value",
    "hand_value",
    "unit",
    "abs_diff",
    "tolerance",
    "verdict",
    "pin_correct",
    "expected_target",
    "reviewer",
    "notes",
]
ANCHOR_COLUMNS = ["pair_id", "slot", "disposition", "pin_number"]


def placement(box, viewport):
    """The overlay rule of piggyback/web/app.js placement(): in, below, outside, partly or none."""
    if not box or not viewport:
        return "none"
    vw, vh = viewport["width"], viewport["height"]
    right = box["x"] + box["width"]
    bottom = box["y"] + box["height"]
    if box["x"] >= 0 and box["y"] >= 0 and right <= vw and bottom <= vh:
        return "in"
    if box["y"] >= vh:
        return "below"
    if bottom <= 0 or right <= 0 or box["x"] >= vw:
        return "outside"
    return "partly"


def box_matches(pin_box, target_box):
    """Shown box vs locked target box: every value within PIN_SLOT_TOLERANCE px (both rounded)."""
    if target_box is None or pin_box is None:
        return False
    got = [js_round(pin_box[k]) for k in ("x", "y", "width", "height")]
    return all(abs(g - t) <= PIN_SLOT_TOLERANCE for g, t in zip(got, target_box, strict=True))


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


def hand_values(target):
    """Hand-checked values from the locked snapshot style. Tracking is converted to px via em."""
    style = target["style"]
    font = fp.px(style.get("font-size"))
    line = style.get("line-height")
    tracking_px = (
        0.0 if style.get("letter-spacing") == "normal" else fp.px(style.get("letter-spacing"))
    )
    if font is None or tracking_px is None:  # unmeasured: no hand value, so the pin cannot pass
        return {"font_size": font, "line_height": None, "letter_spacing": None}
    tracking_em = round(tracking_px / font, 2)
    return {
        "font_size": font,
        "line_height": line if line == "normal" else fp.px(line),
        "letter_spacing": tracking_em * font if font else tracking_px,
    }


def piggyback_values(anchor):
    """Piggyback's own type values from a fingerprint anchor (what the pin displays)."""
    line = anchor.get("line_height")
    return {
        "font_size": fp.px(anchor.get("font_size")),
        "line_height": line if line == "normal" else fp.px(line),
        "letter_spacing": fp.px(anchor.get("letter_spacing"))
        if anchor.get("letter_spacing") != "normal"
        else 0.0,
    }


def score_pair(pair_id, targets, fps, receipts):
    """Rows for one retained pair. fps and targets are dicts keyed by side."""
    pins = gap(fps["reference"], fps["build"])
    by_slot = {}
    for pin in pins:
        if pin.concept is not None and pin.number is not None:
            by_slot[SLOT_OF_CONCEPT[pin.concept]] = pin
    pin_rows, anchor_rows, pinned_total, shown_total, correct_total = [], [], 0, 0, 0
    for slot in SLOTS:
        target = {side: targets[side][slot] for side in ("reference", "build")}
        absent = any(t.get("absent") for t in target.values())
        anchors = {side: fp.anchor(fps[side], slot) for side in ("reference", "build")}
        pin = by_slot.get(slot)
        if absent or any(a is None for a in anchors.values()):
            anchor_rows.append([pair_id, slot, "not found", ""])
            continue
        if pin is None:
            anchor_rows.append([pair_id, slot, "no pin", ""])
            continue
        boxes = {"reference": pin.ref_box, "build": pin.build_box}
        spots = {}
        for side in ("reference", "build"):
            spots[side] = placement(boxes[side], fps[side].get("viewport"))
        shown_sides = [s for s in ("reference", "build") if spots[s] == "in"]
        disposition = (
            "pinned"
            if len(shown_sides) == 2
            else ("partly visible" if "partly" in spots.values() else "no pin")
        )
        anchor_rows.append([pair_id, slot, disposition, pin.number])
        shown = bool(shown_sides)
        element_right = (
            all(box_matches(boxes[s], target[s]["box"]) for s in shown_sides) if shown else False
        )
        value_ok = True
        side_rows = []
        for side in ("reference", "build"):
            hand = hand_values(target[side])
            mine = piggyback_values(anchors[side])
            for prop, unit in (
                ("font_size", "px"),
                ("line_height", "px"),
                ("letter_spacing", "px"),
            ):
                kind = "string" if isinstance(hand[prop], str) else "px"
                ok = within(kind, hand[prop], mine[prop])
                value_ok = value_ok and ok
                side_rows.append(
                    [
                        pair_id,
                        slot,
                        pin.number,
                        side,
                        shown,
                        element_right,
                        prop,
                        mine[prop],
                        hand[prop],
                        unit,
                        None
                        if hand[prop] is None or mine[prop] is None
                        else abs(mine[prop] - hand[prop]),
                        PX_TOLERANCE,
                        "correct" if ok else "incorrect",
                        None,
                        f"{target[side]['tag']} {target[side]['text']!r}",
                        "bot",
                        receipts[side],
                    ]
                )
        correct = shown and element_right and value_ok
        if shown:
            shown_total += 1
            correct_total += int(correct)
        if disposition == "pinned":
            pinned_total += 1
        for row in side_rows:
            row[13] = correct
        pin_rows.extend(side_rows)
    return pin_rows, anchor_rows, shown_total, correct_total, pinned_total


def _private_write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
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


def _parse_replacements(items):
    """'C1-05=R1' pairs: each reserve replaces one failed primary."""
    out = []
    for item in items or []:
        primary, _, reserve = item.partition("=")
        out.append((primary, reserve))
    return out


def required_correct(shown):
    """Fewest correct pins that pass: 90% of the shown pins, rounded up to a whole pin."""
    return -(-PASS_NUMERATOR * shown // PASS_DENOMINATOR)


def run_score(store, run_id, replacements=None):
    run = Path(store) / run_id
    targets = _lock_ok(run)
    data = load_pairs()
    primary = [p["id"] for p in data["primary"]]
    replaced = dict(_parse_replacements(replacements))
    reserves_used = [r for _, r in _parse_replacements(replacements)]
    reserve_order = [r["id"] for r in data["reserves"]]
    if reserves_used != reserve_order[: len(reserves_used)]:
        raise SystemExit("replacements must use reserves in order R1, R2, ...")
    retained = [replaced.get(pid, pid) for pid in primary]  # a reserve takes its primary's row
    unresolved, pin_rows, anchor_rows, pair_rows = [], [], [], []
    shown_total = correct_total = pinned_total = 0
    for pid in retained:
        pair_dir = run / pid
        pair_file = pair_dir / "pair.json"
        if not pair_file.exists() or json.loads(pair_file.read_text())["status"] != "ok":
            unresolved.append(pid)
            continue
        fps, receipts = {}, {}
        for side in ("reference", "build"):
            fps[side] = json.loads(
                (pair_dir / side / "fingerprint.json").read_text(encoding="utf-8")
            )
            receipts[side] = fps[side]["capture"]["captured_at"]
        if pid not in targets:
            unresolved.append(pid)
            continue
        pr, ar, shown, correct, pinned = score_pair(pid, targets[pid], fps, receipts)
        pin_rows += pr
        anchor_rows += ar
        pair_rows.append([pid, shown, correct, pinned, "ok"])
        shown_total += shown
        correct_total += correct
        pinned_total += pinned
    scored = len(retained)
    required = required_correct(shown_total)
    accuracy_ok = shown_total > 0 and correct_total >= required
    anchors_ok = scored == len(primary) and pinned_total >= MIN_AVG_PINNED * scored
    if unresolved or scored != len(primary):
        verdict = "incomplete"
    else:
        verdict = "pass" if accuracy_ok and anchors_ok else "fail"
    report = {
        "verdict": verdict,
        "shown_pins": shown_total,
        "correct_pins": correct_total,
        "required_correct": required,
        "anchors_pinned": pinned_total,
        "retained_pairs": scored,
        "avg_pinned_anchors": round(pinned_total / scored, 2) if scored else None,
        "unresolved": sorted(set(unresolved)),
        "replacements": replacements or [],
    }
    _private_write(run / "pins.csv", _csv(PIN_COLUMNS, pin_rows))
    _private_write(run / "anchors.csv", _csv(ANCHOR_COLUMNS, anchor_rows))
    _private_write(run / "pair_summary.csv", _csv(PAIR_COLUMNS, pair_rows))
    _private_write(run / "verdict.json", json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument("--run", required=True)
    parser.add_argument("--replace", action="append", metavar="C1-05=R1")
    args = parser.parse_args(argv)
    print(json.dumps(run_score(args.store, args.run, args.replace), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
