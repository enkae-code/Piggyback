"""WO-19 round 6: eligibility, lock/score ordering, measure failure evidence, ink and cards.

Non-browser. Synthetic run folders and DOM snapshots only.
"""

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bench.c1 import capture, manifest, score, targets
from piggyback import measure as measure_mod
from piggyback.measure import MeasureError
from tests.test_bench_c1 import FIXTURE, RECEIPT, _meta, _targets_from, evidence

REPO = Path(__file__).resolve().parents[1]


def tmpdir():
    return tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))


def pairs_of(primaries, reserves=()):
    return {
        "primary": [
            {"id": i, "reference": "https://x.test/", "build": "https://x.test/"} for i in primaries
        ],
        "reserves": [
            {"id": i, "reference": "https://x.test/", "build": "https://x.test/"} for i in reserves
        ],
    }


def make_run(root, statuses, classes=None, decisions=None):
    """statuses: {pair_id: pair.json status}. classes: {pair_id: [failure classes]}."""
    run = Path(root) / "run1"
    run.mkdir()
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "piggyback_commit": "abc",
                "chromium_version": "1",
                "spot_check": {"seed": 1, "sample_size": 5, "pool": [], "sample": []},
            }
        )
    )
    for pid, status in statuses.items():
        folder = run / pid
        folder.mkdir()
        (folder / "pair.json").write_text(
            json.dumps({"pair_id": pid, "status": status, "sides": {}})
        )
        for n, cls in enumerate((classes or {}).get(pid, []), start=1):
            side = folder / "reference" / f"attempt-{n}"
            side.mkdir(parents=True)
            (side / "failure.json").write_text(json.dumps({"class": cls}))
    if decisions is not None:
        (run / "eligibility.json").write_text(json.dumps(decisions))
    return run


def decide(pid, value="keep"):
    return {
        "pair_id": pid,
        "decision": value,
        "reason": "test",
        "decided_by": "brodey",
        "decided_at_utc": RECEIPT,
    }


class Eligibility(unittest.TestCase):
    def plan(self, root, data):
        return capture.plan_retained(Path(root) / "run1", data)

    def test_suspected_challenge_needs_a_decision(self):  # item 1
        with tmpdir() as root:
            make_run(root, {"C1-01": "suspected_challenge"})
            with self.assertRaisesRegex(SystemExit, "C1-01"):
                self.plan(root, pairs_of(["C1-01"]))

    def test_keep_scores_the_suspected_pair_as_the_site(self):  # item 1
        with tmpdir() as root:
            make_run(root, {"C1-01": "suspected_challenge"}, decisions=[decide("C1-01")])
            self.assertEqual(self.plan(root, pairs_of(["C1-01"])), [("C1-01", "C1-01")])

    def test_replace_on_suspected_challenge_takes_next_reserve(self):  # item 1
        with tmpdir() as root:
            make_run(
                root,
                {"C1-01": "suspected_challenge", "R1": "ok"},
                decisions=[decide("C1-01", "replace")],
            )
            plan = self.plan(root, pairs_of(["C1-01"], ["R1"]))
            self.assertEqual(plan, [("C1-01", "R1")])

    def test_timeout_is_never_eligible(self):  # item 1
        with tmpdir() as root:
            make_run(
                root,
                {"C1-01": "failed", "R1": "ok"},
                classes={"C1-01": ["timeout"]},
                decisions=[decide("C1-01", "replace")],
            )
            with self.assertRaisesRegex(SystemExit, "eligible"):
                self.plan(root, pairs_of(["C1-01"], ["R1"]))

    def test_replace_needs_the_reserve_captured_first(self):  # item 2
        with tmpdir() as root:
            make_run(
                root,
                {"C1-01": "failed"},
                classes={"C1-01": ["http_status"]},
                decisions=[decide("C1-01", "replace")],
            )
            with self.assertRaisesRegex(SystemExit, "R1 has no pair.json"):
                self.plan(root, pairs_of(["C1-01"], ["R1"]))

    def test_replacements_map_one_to_one_in_reserve_order(self):  # item 4
        with tmpdir() as root:
            make_run(
                root,
                {"C1-01": "failed", "C1-02": "failed", "R1": "ok", "R2": "ok"},
                classes={"C1-01": ["http_status"], "C1-02": ["cross_host_redirect"]},
                decisions=[decide("C1-01", "replace"), decide("C1-02", "replace")],
            )
            plan = self.plan(root, pairs_of(["C1-01", "C1-02"], ["R1", "R2"]))
            self.assertEqual(plan, [("C1-01", "R1"), ("C1-02", "R2")])

    def test_no_reserve_left_is_unresolved_not_a_guess(self):  # item 4
        with tmpdir() as root:
            make_run(
                root,
                {"C1-01": "failed"},
                classes={"C1-01": ["http_status"]},
                decisions=[decide("C1-01", "replace")],
            )
            self.assertEqual(self.plan(root, pairs_of(["C1-01"])), [("C1-01", None)])

    def test_repeated_primary_id_is_refused(self):  # item 4
        with tmpdir() as root:
            make_run(root, {"C1-01": "ok"})
            with self.assertRaisesRegex(SystemExit, "repeats"):
                self.plan(root, pairs_of(["C1-01", "C1-01"]))

    def test_repeated_decision_is_refused(self):  # item 4
        with tmpdir() as root:
            make_run(
                root,
                {"C1-01": "suspected_challenge"},
                decisions=[decide("C1-01"), decide("C1-01", "replace")],
            )
            with self.assertRaisesRegex(SystemExit, "more than one"):
                self.plan(root, pairs_of(["C1-01"]))

    def test_reserve_cannot_be_replaced(self):  # item 1
        with tmpdir() as root:
            make_run(
                root,
                {"C1-01": "ok", "R1": "suspected_challenge"},
                decisions=[decide("R1", "replace")],
            )
            with self.assertRaisesRegex(SystemExit, "only primaries"):
                self.plan(root, pairs_of(["C1-01"], ["R1"]))


class LockAndScoreGates(unittest.TestCase):
    def test_lock_refuses_and_writes_nothing_while_a_decision_is_missing(self):  # item 2
        with tmpdir() as root:
            run = make_run(root, {"C1-01": "suspected_challenge"})
            with (
                mock.patch("bench.c1.targets.check_registered"),
                mock.patch("bench.c1.targets.load_pairs", return_value=pairs_of(["C1-01"])),
                self.assertRaises(SystemExit),
            ):
                targets.lock(root, "run1")
            self.assertEqual(sorted(p.name for p in run.iterdir()), ["C1-01", "manifest.json"])

    def test_lock_checks_registration_before_writing(self):  # item 3
        with tmpdir() as root:
            run = make_run(root, {"C1-01": "ok"})
            with (
                mock.patch(
                    "bench.c1.targets.check_registered", side_effect=SystemExit("refusing: HEAD")
                ),
                mock.patch("bench.c1.targets.load_pairs", return_value=pairs_of(["C1-01"])),
            ):
                with self.assertRaisesRegex(SystemExit, "HEAD"):
                    targets.lock(root, "run1")
            self.assertFalse((run / "targets.json").exists())

    def test_score_checks_registration(self):  # item 3
        with tmpdir() as root:
            make_run(root, {"C1-01": "ok"})
            with (
                mock.patch(
                    "bench.c1.score.check_registered", side_effect=SystemExit("refusing: HEAD")
                ),
                self.assertRaisesRegex(SystemExit, "HEAD"),
            ):
                score.run_score(root, "run1")

    def test_score_refuses_when_verdict_exists(self):  # item 3
        with tmpdir() as root:
            run = make_run(root, {"C1-01": "ok"})
            (run / "verdict.json").write_text("{}")
            with mock.patch("bench.c1.score.check_registered"):
                with self.assertRaisesRegex(SystemExit, "first scoring is final"):
                    score.run_score(root, "run1")
            self.assertEqual((run / "verdict.json").read_text(), "{}")

    def test_score_refuses_before_pins_while_a_decision_is_missing(self):  # item 2
        with tmpdir() as root:
            run = make_run(root, {"C1-01": "suspected_challenge"})
            (run / "targets.json").write_text("{}")
            sha = hashlib.sha256(b"{}").hexdigest()
            (run / "targets.lock.json").write_text(json.dumps({"targets_sha256": sha}))
            with (
                mock.patch("bench.c1.score.check_registered"),
                mock.patch("bench.c1.score.load_pairs", return_value=pairs_of(["C1-01"])),
                self.assertRaises(SystemExit),
            ):
                score.run_score(root, "run1")
            self.assertFalse((run / "verdict.json").exists())
            self.assertFalse((run / "pins.csv").exists())

    def test_replaced_primary_gets_a_summary_row(self):  # item 10
        with tmpdir() as root:
            run = make_run(
                root,
                {"C1-01": "failed", "R1": "ok"},
                classes={"C1-01": ["http_status"]},
                decisions=[decide("C1-01", "replace")],
            )
            (run / "targets.json").write_text("{}")
            sha = hashlib.sha256(b"{}").hexdigest()
            (run / "targets.lock.json").write_text(json.dumps({"targets_sha256": sha}))
            with (
                mock.patch("bench.c1.score.check_registered"),
                mock.patch("bench.c1.score.load_pairs", return_value=pairs_of(["C1-01"], ["R1"])),
                mock.patch("bench.c1.score._load_pair", return_value=None),
            ):
                score.run_score(root, "run1")
            rows = (run / "pair_summary.csv").read_text().splitlines()
            self.assertIn("C1-01,0,0,0,replaced by R1", rows)


class PinRowsAndStore(unittest.TestCase):
    def setUp(self):
        self.fps = {
            side: json.loads((FIXTURE / f"{side}.json").read_text(encoding="utf-8"))
            for side in ("reference", "build")
        }

    def test_one_pin_row_per_numbered_pin_and_properties_separate(self):  # item 12
        targets_by_side = {side: _targets_from(self.fps[side]) for side in self.fps}
        pin_rows, prop_rows, _, _, _, _, _ = score.score_pair(
            "D-01", targets_by_side, self.fps, _meta()
        )
        keys = [(r[1], r[2]) for r in pin_rows]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(len(r) == len(score.PIN_COLUMNS) for r in pin_rows))
        self.assertNotIn("side", score.PIN_COLUMNS)
        self.assertIn("side", score.PROP_COLUMNS)
        self.assertTrue(all(len(r) == len(score.PROP_COLUMNS) for r in prop_rows))

    def test_symlinked_run_folder_is_refused(self):  # item 11
        with tmpdir() as root:
            victim = Path(root) / "elsewhere"
            victim.mkdir()
            store = Path(root) / "store"
            store.mkdir()
            (store / "run1").symlink_to(victim)
            with self.assertRaisesRegex(SystemExit, "symbolic link"):
                manifest.run_folder(str(store), "run1")

    def test_run_folder_inside_the_repo_is_refused(self):  # item 11
        with self.assertRaisesRegex(SystemExit, "not inside the store"):
            manifest.run_folder(str(REPO / "bench"), "run1")

    def test_run_folder_in_the_store_is_returned(self):  # item 11
        with tmpdir() as root:
            self.assertEqual(manifest.run_folder(root, "run1"), Path(root) / "run1")


class InkAndCards(unittest.TestCase):
    def test_ink_box_covers_block_descendants(self):  # item 8
        # h1 box 0,0,400,200 with a text box 0,0,100,20; a block span and a div inside it.
        ev = evidence(
            [
                _el("h1", box=(0, 0, 400, 200), text="Hi", font_size="48px"),
                _el("span", parent=0, box=(0, 60, 250, 40)),
                _el("div", parent=2, box=(0, 120, 300, 30)),
            ]
        )
        ev["documents"][0]["textBoxes"]["bounds"][0] = [0, 0, 100, 20]
        heading = targets.lock_targets(ev)["heading"]
        self.assertEqual(heading["box"], [0, 0, 300, 150])

    def test_equal_card_groups_tie_by_parent_then_signature(self):  # item 9
        def card(parent, cls, x):
            return _el("div", parent=parent, box=(x, 0, 200, 100), text="t", attrs={"class": cls})

        ev = evidence(
            [
                _el("div", box=(0, 0, 1000, 1000)),  # P1, node 0
                _el("div", box=(0, 0, 1000, 1000)),  # P2, node 1
                _el("div", parent=0, box=(0, 0, 50, 50), attrs={"class": "a"}),  # node 2
                card(1, "b", 0),  # node 3, text node 4
                card(1, "b", 220),  # node 5, text node 6
                card(1, "b", 440),  # node 7, text node 8
                card(0, "c", 660),  # node 9, text node 10
                card(0, "c", 880),  # node 11, text node 12
                card(0, "c", 1100),  # node 13, text node 14
            ]
        )
        card_target = targets.lock_targets(ev)["card"]
        # Parent P1 is seen first, so its 'c' group precedes P2's 'b' group: 'c' wins the tie.
        self.assertEqual(card_target["count"], 3)
        self.assertEqual(card_target["box"][0], 660)


def _el(tag, parent=-1, box=None, text=None, attrs=None, **style):
    from tests.test_bench_c1 import el

    return el(tag, parent=parent, box=box, text=text, attrs=attrs, **style)


class MeasureEvidence(unittest.TestCase):
    def test_refusal_publishes_the_screenshot_when_requested(self):  # item 5
        with tmpdir() as root:
            tmp, dest = Path(root) / "tmp.png", Path(root) / "shot.png"
            tmp_path = str(tmp)

            class Moved:
                url = "https://other.example/"

                def screenshot(self, path, type):  # noqa: A002
                    Path(path).write_bytes(b"png")

            req = {
                "url": "https://ref.example/",
                "failure_screenshot": True,
                "screenshot": str(dest),
            }
            with self.assertRaises(MeasureError) as ctx:
                measure_mod._refuse_if_moved(200, req, Moved(), tmp_path)
            self.assertEqual(ctx.exception.screenshot, str(dest))
            self.assertTrue(dest.exists())

    def test_default_refusal_takes_no_screenshot(self):  # item 5
        class Moved:
            url = "https://other.example/"

            def screenshot(self, path, type):  # noqa: A002
                raise AssertionError("no screenshot by default")

        with self.assertRaises(MeasureError) as ctx:
            measure_mod._refuse_if_moved(200, {"url": "https://ref.example/"}, Moved())
        self.assertIsNone(ctx.exception.screenshot)

    def test_worker_error_keeps_the_full_text(self):  # item 5
        def boom(_req):
            raise RuntimeError("first line\nsecond line")

        with (
            mock.patch.object(measure_mod, "_capture", boom),
            mock.patch("sys.stdin", io.StringIO("{}")),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            measure_mod._worker()
        self.assertIn("second line", json.loads(out.getvalue())["error"])

    def test_capture_asks_for_a_failure_screenshot(self):  # item 5
        seen = {}

        def fake(url, *, screenshot, evidence, failure_screenshot=False, **_):
            seen["flag"] = failure_screenshot
            raise MeasureError("page answered HTTP 500", status=500, final_url=url)

        with tmpdir() as root:
            capture.capture_side("https://x.test/", Path(root) / "s", measure_fn=fake)
        self.assertTrue(seen["flag"])

    def test_paragraph_text_drops_only_the_failing_node(self):  # item 6
        snapshot = {
            "documents": [
                {
                    "nodes": {
                        "nodeType": [1, 1],
                        "nodeName": [0, 0],
                        "backendNodeId": [10, 11],
                    }
                }
            ],
            "strings": ["P"],
        }

        class Session:
            def send(self, method, params):
                if method == "DOM.resolveNode":
                    if params["backendNodeId"] == 10:
                        raise RuntimeError("node detached")
                    return {"object": {"objectId": "o11"}}
                return {"result": {"value": "kept"}}

        texts = measure_mod._paragraph_texts(Session(), snapshot)
        self.assertEqual(texts, {"11": "kept"})


class CrashRecords(unittest.TestCase):
    def test_crash_outside_measure_error_writes_a_failed_pair_and_reraises(self):  # item 7
        pair = {"id": "C1-01", "reference": "https://x.test/", "build": "https://x.test/"}

        def interrupted(*_a, **_k):
            raise KeyboardInterrupt()

        with tmpdir() as root:
            run = Path(root) / "run1"
            run.mkdir()
            with self.assertRaises(KeyboardInterrupt):
                capture.capture_pair(pair, run, measure_fn=interrupted)
            record = json.loads((run / "C1-01" / "pair.json").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["class"], "other")
        self.assertIn("error", record)


if __name__ == "__main__":
    with contextlib.redirect_stdout(io.StringIO()):
        unittest.main()
