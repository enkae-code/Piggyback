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


def _hand_value(prop, raw, font):
    if prop == "font_size":
        value = font
    elif prop == "line_height":
        value = raw if raw == "normal" else fp.px(raw)
    else:  # tracking is locked in px, compared through em to the gap rule's 0.01em grain
        tracking = 0.0 if raw == "normal" else fp.px(raw)
        value = round(tracking / font, 2) * font if tracking is not None and font else None
    if value is None:
        raise HarnessDefect(f"no locked hand value for {HAND_KEYS[prop]}: {raw!r}")
    return value


def hand_values(target, props):
    """Hand-checked values for the named properties, from the locked snapshot style."""
    style = target["style"]
    font = fp.px(style.get(HAND_KEYS["font_size"]))
    return {prop: _hand_value(prop, style.get(HAND_KEYS[prop]), font) for prop in props}


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


SIDE_KEYS = ("reference", "build")
PIN_CORRECT = PIN_COLUMNS.index("pin_correct")


def _anchor_pins(pins):
    """Numbered pins for the five anchor slots, keyed by slot. Page-level pins are left out."""
    return {
        SLOT_OF_CONCEPT[pin.concept]: pin
        for pin in pins
        if pin.number is not None and pin.concept in SLOT_OF_CONCEPT
    }


def _value_rows(pair_id, slot, pin, target, anchors, shown, element_right, receipts):
    """One row per side and property: hand-checked value against Piggyback's own value."""
    rows, value_ok = [], True
    props = reported_props(pin.detail)
    for side in SIDE_KEYS:
        hand = hand_values(target[side], props)
        mine = piggyback_values(anchors[side])
        for prop in props:
            ok = within("string" if isinstance(hand[prop], str) else "px", hand[prop], mine[prop])
            numeric = isinstance(hand[prop], float) and isinstance(mine[prop], float)
            value_ok = value_ok and ok
            rows.append(
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
                    "px",
                    abs(mine[prop] - hand[prop]) if numeric else None,
                    PX_TOLERANCE,
                    "correct" if ok else "incorrect",
                    None,
                    f"{target[side]['tag']} {target[side]['text']!r}",
                    "bot",
                    receipts[side],
                ]
            )
    return rows, value_ok


def _disposition(spots):
    """Anchor disposition: pinned only when both sides show a fully visible overlay."""
    if all(spot == "in" for spot in spots.values()):
        return "pinned"
    return "partly visible" if "partly" in spots.values() else "no pin"


def _slot_rows(pair_id, slot, pin, target, anchors, fps, receipts):
    """Anchor disposition, then pin rows for one slot. Returns (anchor_row, pin_rows, counts)."""
    boxes = {"reference": pin.ref_box, "build": pin.build_box}
    spots = {side: placement(boxes[side], fps[side].get("viewport")) for side in SIDE_KEYS}
    shown_sides = [side for side in SIDE_KEYS if spots[side] == "in"]
    disposition = _disposition(spots)
    shown = bool(shown_sides)
    element_right = shown and all(box_matches(boxes[s], target[s]["box"]) for s in shown_sides)
    pin_rows, value_ok = _value_rows(
        pair_id, slot, pin, target, anchors, shown, element_right, receipts
    )
    correct = shown and element_right and value_ok
    for row in pin_rows:
        row[PIN_CORRECT] = correct
    counts = (int(shown), int(correct), int(disposition == "pinned"))
    return [pair_id, slot, disposition, pin.number], pin_rows, counts


def _missing_row(pair_id, slot, target, anchors, pin):
    """Disposition for a slot that cannot be scored, else None."""
    if any(t.get("absent") for t in target.values()) or any(a is None for a in anchors.values()):
        return [pair_id, slot, "not found", ""]
    if pin is None:
        return [pair_id, slot, "no pin", ""]
    return None


def score_pair(pair_id, targets, fps, receipts):
    """Rows for one retained pair: (pin_rows, anchor_rows, shown, correct, pinned, skipped).

    skipped counts numbered page-level pins (accent, container, motion): outside pin accuracy.
    """
    pins = gap(fps["reference"], fps["build"])
    by_slot = _anchor_pins(pins)
    skipped = sum(
        1 for pin in pins if pin.number is not None and pin.concept not in SLOT_OF_CONCEPT
    )
    pin_rows, anchor_rows = [], []
    shown_total = correct_total = pinned_total = 0
    for slot in SLOTS:
        target = {side: targets[side][slot] for side in SIDE_KEYS}
        anchors = {side: fp.anchor(fps[side], slot) for side in SIDE_KEYS}
        pin = by_slot.get(slot)
        missing = _missing_row(pair_id, slot, target, anchors, pin)
        if missing is not None:
            anchor_rows.append(missing)
            continue
        anchor_row, rows, (shown, correct, pinned) = _slot_rows(
            pair_id, slot, pin, target, anchors, fps, receipts
        )
        anchor_rows.append(anchor_row)
        pin_rows.extend(rows)
        shown_total += shown
        correct_total += correct
        pinned_total += pinned
    return pin_rows, anchor_rows, shown_total, correct_total, pinned_total, skipped


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


def _retained_ids(data, replacements):
    """Primary IDs, each named primary replaced by its reserve. Reserves are used in order."""
    replaced = dict(_parse_replacements(replacements))
    reserves_used = [r for _, r in _parse_replacements(replacements)]
    reserve_order = [r["id"] for r in data["reserves"]]
    if reserves_used != reserve_order[: len(reserves_used)]:
        raise SystemExit("replacements must use reserves in order R1, R2, ...")
    return [replaced.get(p["id"], p["id"]) for p in data["primary"]]  # a reserve takes its row


def _load_pair(run, pid, targets):
    """(fingerprints, receipts) for a pair that captured ok and has locked targets, else None."""
    pair_dir = run / pid
    pair_file = pair_dir / "pair.json"
    if not pair_file.exists() or json.loads(pair_file.read_text())["status"] != "ok":
        return None
    if pid not in targets:
        return None
    fps, receipts = {}, {}
    for side in SIDE_KEYS:
        fps[side] = json.loads((pair_dir / side / "fingerprint.json").read_text(encoding="utf-8"))
        receipts[side] = fps[side]["capture"]["captured_at"]
    return fps, receipts


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


def run_score(store, run_id, replacements=None):
    run = Path(store) / run_id
    targets = _lock_ok(run)
    data = load_pairs()
    retained = _retained_ids(data, replacements)
    unresolved, pin_rows, anchor_rows, pair_rows = [], [], [], []
    shown_total = correct_total = pinned_total = skipped_total = 0
    for pid in retained:
        loaded = _load_pair(run, pid, targets)
        if loaded is None:
            unresolved.append(pid)
            continue
        pr, ar, shown, correct, pinned, skipped = score_pair(pid, targets[pid], *loaded)
        pin_rows += pr
        anchor_rows += ar
        pair_rows.append([pid, shown, correct, pinned, "ok"])
        shown_total += shown
        correct_total += correct
        pinned_total += pinned
        skipped_total += skipped
    scored = len(pair_rows)  # resolved pairs only: an unresolved pair is counted as unresolved
    verdict, required = _verdict(
        scored, len(data["primary"]), unresolved, shown_total, correct_total, pinned_total
    )
    report = {
        "verdict": verdict,
        "shown_pins": shown_total,
        "correct_pins": correct_total,
        "required_correct": required,
        "anchors_pinned": pinned_total,
        "page_level_pins_skipped": skipped_total,
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
