"""Round-4 C1 regressions: registration, replacements, cards, pins, type, audit, safety."""

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from bench.c1 import capture, manifest, score, targets
from bench.c1.snapshot import Snapshot
from piggyback import fingerprint as fp
from tests.test_bench_c1 import el, evidence, target_of

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "c1"


def tmpdir():
    return tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))


class TargetLockRefusals(unittest.TestCase):
    def test_lock_refuses_without_manifest_and_writes_nothing(self):
        with tmpdir() as root:
            run = Path(root) / "run1"
            (run / "C1-01").mkdir(parents=True)
            (run / "C1-01" / "pair.json").write_text(json.dumps({"status": "failed", "sides": {}}))
            with self.assertRaises(SystemExit) as ctx:
                targets.lock(root, "run1")
            self.assertIn("no manifest", str(ctx.exception))
            self.assertEqual([p.name for p in run.iterdir()], ["C1-01"])

    def test_lock_refuses_when_a_primary_has_no_pair_and_writes_nothing(self):
        with tmpdir() as root:
            run = Path(root) / "run1"
            (run / "C1-01").mkdir(parents=True)
            (run / "C1-01" / "pair.json").write_text(json.dumps({"status": "failed", "sides": {}}))
            manifest_data = {"spot_check": {"pool": ["C1-01", "C1-02"]}}
            (run / "manifest.json").write_text(json.dumps(manifest_data))
            primaries = {"primary": [{"id": "C1-01"}, {"id": "C1-02"}], "reserves": []}
            with (
                mock.patch("bench.c1.targets.check_registered"),
                mock.patch("bench.c1.targets.load_pairs", return_value=primaries),
                self.assertRaises(SystemExit) as ctx,
            ):
                targets.lock(root, "run1")
            self.assertIn("C1-02", str(ctx.exception))
            self.assertEqual(sorted(p.name for p in run.iterdir()), ["C1-01", "manifest.json"])


class SpotCheckPool(unittest.TestCase):
    def test_pool_smaller_than_sample_is_refused_with_a_message(self):
        with self.assertRaises(SystemExit) as ctx:
            manifest.spot_check(["C1-01", "C1-02"], 1)
        self.assertIn("exceeds the pool of 2", str(ctx.exception))


class Registration(unittest.TestCase):
    def test_capture_refuses_without_manifest(self):
        with tmpdir() as root:
            with self.assertRaises(SystemExit) as ctx:
                capture.main(["--store", root, "--run", "run1", "C1-01"])
            self.assertIn("no manifest", str(ctx.exception))

    def test_capture_refuses_when_head_is_not_manifest_commit(self):
        with tmpdir() as root:
            run = Path(root) / "run1"
            run.mkdir()
            (run / "manifest.json").write_text(json.dumps({"piggyback_commit": "0" * 40}))
            with self.assertRaises(SystemExit) as ctx:
                manifest.check_registered(json.loads((run / "manifest.json").read_text()))
            self.assertIn("is not the manifest commit", str(ctx.exception))

    def test_score_refuses_without_manifest(self):
        with tmpdir() as root:
            (Path(root) / "run1").mkdir()
            with mock.patch.object(score, "_lock_ok", return_value={}):
                with self.assertRaises(SystemExit) as ctx:
                    score.run_score(root, "run1")
            self.assertIn("no manifest", str(ctx.exception))

    def test_receipt_chromium_must_equal_manifest(self):
        with tmpdir() as root:
            run = Path(root) / "run1"
            pair_dir = run / "C1-01"
            for side in ("reference", "build"):
                folder = pair_dir / side / "attempt-1"
                folder.mkdir(parents=True)
                fp_doc = {"capture": {"browser_version": "1.0", "final_url": "u"}}
                (folder / "fingerprint.json").write_text(json.dumps(fp_doc))
            pair = {
                "status": "ok",
                "sides": {s: {"requested_url": "u", "attempt": 1} for s in ("reference", "build")},
            }
            (pair_dir / "pair.json").write_text(json.dumps(pair))
            with self.assertRaises(SystemExit) as ctx:
                score._load_pair(run, "C1-01", {"C1-01": {}}, chromium="2.0")
            self.assertIn("manifest registered 2.0", str(ctx.exception))

    def test_manifest_publishes_0600_without_writing_through_a_link(self):
        with tmpdir() as root:
            run = Path(root) / "run1"
            run.mkdir()
            victim = Path(root) / "victim"
            victim.write_text("keep")
            (run / "manifest.json").symlink_to(victim)
            with (
                mock.patch.object(manifest, "clean_commit", return_value="abc"),
                mock.patch.object(
                    manifest, "bundled_chromium", return_value=("156.0", "bundled chromium")
                ),
            ):
                with self.assertRaises(SystemExit):  # existing link: already locked, refused
                    manifest.build(root, "run1", 1)
            self.assertEqual(victim.read_text(), "keep")


class CardPool(unittest.TestCase):
    def cards_below_fold(self):
        parent = el("div", box=(0, 0, 1440, 1400))
        cards = [
            el("div", parent=0, box=(x, 1000, 200, 120), text=f"c{x}", attrs={"class": "c"})
            for x in (0, 220, 440)
        ]
        return [parent] + cards

    def test_below_fold_group_is_a_card_group(self):
        t = target_of(self.cards_below_fold(), "card")
        self.assertEqual((t["count"], t["text"]), (3, "c0"))

    def test_pseudo_element_node_is_not_a_card(self):
        ev = evidence(self.cards_below_fold())
        pseudo_idx = 3  # node order: parent, card, its text, card, its text, card, its text
        ev["documents"][0]["nodes"]["pseudoType"] = {"index": [pseudo_idx], "value": [0]}
        t = targets.lock_targets(ev)["card"]
        self.assertEqual(t, {"absent": True})  # the pseudo node drops out: two cards, not three

    def test_empty_class_is_no_class(self):
        cards = [
            el("div", parent=0, box=(x, 0, 200, 120), text=f"c{x}", attrs={"class": ""})
            for x in (0, 220, 440)
        ]
        t = target_of([el("div", box=(0, 0, 1440, 900))] + cards, "card")
        self.assertEqual(t, {"absent": True})


class ShownPinWithAbsentTarget(unittest.TestCase):
    def setUp(self):
        self.fp = json.loads((FIXTURE / "reference.json").read_text(encoding="utf-8"))
        self.anchor = fp.anchor(self.fp, "heading")
        self.box = {"x": 60, "y": 757, "width": 169, "height": 35}

    def test_shown_pin_with_absent_build_target_is_incorrect_and_counted(self):
        target = {
            "reference": targets.lock_targets(
                evidence([el("h1", box=(60, 757, 169, 35), text="x", font_size="32px")])
            )["heading"]
        }
        target["build"] = {"absent": True}
        pin = types.SimpleNamespace(
            number=1,
            concept="heading",
            detail="heading font 32px",
            ref_box=self.box,
            build_box=self.box,
        )
        fps = {"reference": self.fp, "build": self.fp}
        meta = {
            side: {"requested_url": "u", "final_url": "u", "receipt": "r"}
            for side in ("reference", "build")
        }
        anchor_row, pin_row, _, (shown, correct, pinned) = score._slot_rows(
            "D-01",
            "heading",
            pin,
            target,
            {"reference": self.anchor, "build": self.anchor},
            fps,
            meta,
        )
        self.assertEqual((shown, correct), (1, 0))  # in the denominator, scored incorrect
        self.assertIs(pin_row[score.PIN_COLUMNS.index("element_right")], False)
        self.assertEqual(anchor_row[2], "pinned")


class DisplayedTypeValues(unittest.TestCase):
    def test_tracking_rounds_to_hundredths_of_an_em_then_to_px(self):
        self.assertEqual(score._tracking(0.05, 16, 16), 0.0)  # 0.003125em rounds to 0.00
        self.assertEqual(score._tracking(5.6, 400, 400), 4.0)  # 0.014em rounds to 0.01
        self.assertEqual(score._tracking(-1.0, 16, 16), -0.96)  # -0.0625em rounds to -0.06em

    def test_size_and_line_show_one_decimal(self):
        anchor = {"font_size": "13.33333px", "line_height": "17.9999px", "letter_spacing": "normal"}
        mine = score.piggyback_values(anchor, 13.33333)
        self.assertEqual(mine["font_size"], 13.3)
        self.assertEqual(mine["line_height"], 18.0)

    def test_tracking_is_zero_for_normal(self):
        anchor = {"font_size": "16px", "line_height": "normal", "letter_spacing": "normal"}
        self.assertEqual(score.piggyback_values(anchor, 16)["letter_spacing"], 0.0)


class BodyText(unittest.TestCase):
    def test_recorded_inner_text_sets_body_length(self):
        ev = evidence([el("p", box=(0, 0, 400, 80), text="short")])
        nodes = ev["documents"][0]["nodes"]
        p_idx = next(i for i, n in enumerate(nodes["nodeName"]) if ev["strings"][n] == "P")
        backend = nodes["backendNodeId"][p_idx]
        ev["inner_text"] = {str(backend): "a" * 45}
        self.assertEqual(targets.lock_targets(ev)["body"]["tag"], "p")
        ev["inner_text"] = {}
        self.assertEqual(targets.lock_targets(ev)["body"], {"absent": True})
        self.assertEqual(Snapshot(ev).paragraph_inner_text(p_idx), "short")


class AuditAndSafety(unittest.TestCase):
    def test_pins_csv_carries_urls_receipts_and_boxes(self):
        for column in (
            "reference_requested_url",
            "reference_final_url",
            "reference_receipt",
            "build_requested_url",
            "build_final_url",
            "build_receipt",
            "reference_shown_box",
            "build_shown_box",
        ):
            self.assertIn(column, score.PIN_COLUMNS)

    def test_targets_json_replaces_a_link_and_never_writes_through(self):
        with tmpdir() as root:
            run = Path(root) / "run1"
            run.mkdir()
            victim = Path(root) / "victim"
            victim.write_text("keep")
            (run / "targets.json").symlink_to(victim)
            (run / "manifest.json").write_text(json.dumps({"spot_check": {"pool": []}}))
            with (
                mock.patch("bench.c1.targets.check_registered"),
                mock.patch(
                    "bench.c1.targets.load_pairs", return_value={"primary": [], "reserves": []}
                ),
            ):
                targets.lock(root, "run1")
            self.assertEqual(victim.read_text(), "keep")
            self.assertFalse((run / "targets.json").is_symlink())

    def test_run_id_must_be_one_path_segment(self):
        for bad in ("../x", "a/b", "", ".."):
            with self.assertRaises(SystemExit):
                manifest.run_folder("/tmp", bad)
        self.assertEqual(manifest.run_folder("/tmp", "run1"), Path("/tmp/run1"))


if __name__ == "__main__":
    unittest.main()
