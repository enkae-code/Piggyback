"""Non-browser C1 harness tests: synthetic DOMSnapshot fixtures, tolerance, lock, capture."""

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bench.c1 import capture, manifest, score, targets
from bench.c1.snapshot import Snapshot
from piggyback import fingerprint as fp
from piggyback.gap import ANCHORS, gap
from piggyback.measure import EVIDENCE_STYLES, MeasureError

REPO = Path(__file__).resolve().parents[1]
BASE = {
    "display": "block",
    "visibility": "visible",
    "opacity": "1",
    "font-size": "16px",
    "line-height": "normal",
    "letter-spacing": "normal",
    "background-color": "rgba(0, 0, 0, 0)",
    "font-family": "serif",
    "color": "rgb(0, 0, 0)",
    "font-weight": "400",
    "border-top-left-radius": "0px",
    "box-shadow": "none",
    "padding-top": "0px",
    "padding-right": "0px",
    "padding-bottom": "0px",
    "padding-left": "0px",
}


def el(tag, parent=-1, box=None, text=None, attrs=None, **style):
    """One element spec. text: a text child with the same box (or none when box is None)."""
    return {
        "tag": tag,
        "parent": parent,
        "box": box,
        "text": text,
        "attrs": attrs or {},
        "style": {**BASE, **{k.replace("_", "-"): v for k, v in style.items()}},
    }


def evidence(elements, viewport=(1440, 900), scroll=(0, 0)):
    """A DOMSnapshot-shaped dict (what piggyback.measure saves) built from element specs."""
    strings = [""]

    def s(value):
        if value not in strings:
            strings.append(value)
        return strings.index(value)

    nodes = {"nodeType": [], "parentIndex": [], "nodeName": [], "nodeValue": [], "attributes": []}
    layout = {"nodeIndex": [], "styles": [], "bounds": []}
    text_boxes = {"layoutIndex": [], "bounds": []}

    def add(kind, parent, name, value, attrs):
        nodes["nodeType"].append(kind)
        nodes["parentIndex"].append(parent)
        nodes["nodeName"].append(s(name))
        nodes["nodeValue"].append(s(value))
        nodes["attributes"].append(attrs)
        return len(nodes["nodeType"]) - 1

    for spec in elements:
        flat = []
        for key, value in spec["attrs"].items():
            flat += [s(key), s(value)]
        index = add(1, spec["parent"], spec["tag"].upper(), "", flat)
        if spec["box"] is not None:
            layout["nodeIndex"].append(index)
            layout["styles"].append([s(spec["style"][k]) for k in EVIDENCE_STYLES])
            layout["bounds"].append(list(spec["box"]))
        if spec["text"] is not None:
            text_index = add(3, index, "#text", spec["text"], [])
            if spec["box"] is not None:
                layout["nodeIndex"].append(text_index)
                layout["styles"].append([s(spec["style"][k]) for k in EVIDENCE_STYLES])
                layout["bounds"].append(list(spec["box"]))
                text_boxes["layoutIndex"].append(len(layout["nodeIndex"]) - 1)
                text_boxes["bounds"].append(list(spec["box"]))
    return {
        "styles": EVIDENCE_STYLES,
        "page": {
            "url": "https://x.test/",
            "title": "t",
            "scroll": list(scroll),
            "viewport": list(viewport),
            "text": "",
        },
        "strings": strings,
        "documents": [{"nodes": nodes, "layout": layout, "textBoxes": text_boxes}],
    }


def target_of(elements, slot, viewport=(1440, 900)):
    return targets.lock_targets(evidence(elements, viewport))[slot]


class TargetRules(unittest.TestCase):
    def test_heading_is_largest_font(self):
        t = target_of(
            [
                el("h2", box=(0, 0, 300, 40), text="small", font_size="20px"),
                el("h1", box=(0, 50, 300, 80), text="big", font_size="48px"),
            ],
            "heading",
        )
        self.assertEqual(t["tag"], "h1")

    def test_heading_tie_goes_to_larger_clipped_area_then_dom_order(self):
        t = target_of(
            [
                el("h1", box=(0, 0, 100, 40), text="a", font_size="32px"),
                el("h2", box=(0, 50, 300, 40), text="b", font_size="32px"),
            ],
            "heading",
        )
        self.assertEqual(t["tag"], "h2")
        t = target_of(
            [
                el("h1", box=(0, 0, 100, 40), text="a", font_size="32px"),
                el("h2", box=(0, 50, 100, 40), text="b", font_size="32px"),
            ],
            "heading",
        )
        self.assertEqual(t["tag"], "h1")

    def test_heading_must_be_rendered_and_on_screen(self):
        for bad in (
            dict(display="none"),
            dict(visibility="hidden"),
            dict(opacity="0"),
        ):
            t = target_of(
                [el("h1", box=(0, 0, 100, 40), text="x", font_size="60px", **bad)], "heading"
            )
            self.assertEqual(t, {"absent": True}, bad)
        off = target_of([el("h1", box=(0, 1000, 100, 40), text="x", font_size="60px")], "heading")
        self.assertEqual(off, {"absent": True})
        right = target_of([el("h1", box=(1500, 0, 100, 40), text="x", font_size="60px")], "heading")
        self.assertEqual(right, {"absent": True})

    def test_heading_empty_text_skipped(self):
        t = target_of(
            [
                el("h1", box=(0, 0, 100, 40), text="", font_size="60px"),
                el("h3", box=(0, 0, 100, 40), text="ok", font_size="20px"),
            ],
            "heading",
        )
        self.assertEqual(t["tag"], "h3")

    def test_heading_box_is_text_ink_clipped_to_element(self):
        # The element is 300 wide, but the text ink covers only 120 px of it.
        spec = el("h1", box=(0, 0, 300, 60), text="hi", font_size="40px")
        spec["text_box"] = (10, 10, 120, 30)
        data = evidence([spec])
        data["documents"][0]["textBoxes"] = {"layoutIndex": [1], "bounds": [[10, 10, 120, 30]]}
        t = targets.lock_targets(data)["heading"]
        self.assertEqual(t["box"], [10, 10, 120, 30])

    def test_body_first_p_with_forty_chars(self):
        short = "too short"
        long = "x" * 40
        t = target_of(
            [
                el("p", box=(0, 0, 300, 20), text=short),
                el("p", box=(0, 30, 300, 20), text=long),
                el("p", box=(0, 60, 300, 20), text="y" * 80),
            ],
            "body",
        )
        self.assertEqual(t["text"], "x" * 40)

    def test_cta_prefers_painted_background_over_larger_unpainted(self):
        t = target_of(
            [
                el("button", box=(0, 0, 400, 100), text="huge plain", font_size="16px"),
                el("button", box=(0, 200, 100, 40), text="go", background_color="rgb(10, 20, 30)"),
            ],
            "cta",
        )
        self.assertEqual(t["text"], "go")

    def test_cta_falls_back_to_area_when_nothing_painted(self):
        t = target_of(
            [
                el("button", box=(0, 0, 100, 40), text="small"),
                el("a", box=(0, 60, 200, 40), text="big", attrs={"class": "Main-BTN"}),
            ],
            "cta",
        )
        self.assertEqual(t["text"], "big")

    def test_cta_candidate_rules(self):
        plain_link = el("a", box=(0, 0, 200, 40), text="link")
        self.assertEqual(target_of([plain_link], "cta"), {"absent": True})
        role = el("a", box=(0, 0, 200, 40), text="role", attrs={"role": "button"})
        self.assertEqual(target_of([role], "cta")["text"], "role")

    def test_nav_first_in_dom_order_or_absent(self):
        self.assertEqual(target_of([el("div", box=(0, 0, 10, 10))], "nav"), {"absent": True})
        t = target_of(
            [
                el("div", box=(0, 0, 500, 60), attrs={"role": "navigation"}),
                el("nav", box=(0, 0, 500, 60)),
            ],
            "nav",
        )
        self.assertEqual(t["tag"], "div")

    def test_card_needs_three_qualifying_siblings(self):
        card = dict(box=(0, 0, 200, 120), attrs={"class": "c"}, parent=0)
        two = [
            el("div", parent=-1, box=(0, 0, 900, 900)),
            el("div", **{**card, "text": "a"}),
            el("div", **{**card, "text": "b"}),
        ]
        self.assertEqual(target_of(two, "card"), {"absent": True})
        three = [el("div", parent=-1, box=(0, 0, 900, 900))] + [
            el("div", **{**card, "box": (i * 210, 0, 200, 120), "text": f"c{i}"}) for i in range(3)
        ]
        t = target_of(three, "card")
        self.assertEqual((t["count"], t["text"]), (3, "c0"))

    def test_card_excludes_offscreen_small_and_svg(self):
        parent = el("div", box=(0, 0, 900, 900))
        cards = [el("div", parent=0, box=(0, 0, 100, 50), text="small", attrs={"class": "c"})]
        cards += [
            el("div", parent=0, box=(i * 210, 0, 200, 120), text=f"x{i}", attrs={"class": "c"})
            for i in range(2)
        ]
        cards += [el("div", parent=0, box=(1300, 0, 200, 120), text="edge", attrs={"class": "c"})]
        self.assertEqual(target_of([parent] + cards, "card"), {"absent": True})

    def test_card_svg_and_controls_excluded(self):
        parent = el("div", box=(0, 0, 900, 900))
        svg = el("svg", parent=0, box=(0, 0, 400, 400))
        groups = [
            el("g", parent=1, box=(i * 150, 0, 140, 100), text="g", attrs={"class": "x"})
            for i in range(3)
        ]
        controls = [
            el("button", parent=0, box=(i * 210, 0, 200, 120), text="b", attrs={"class": "x"})
            for i in range(3)
        ]
        self.assertEqual(target_of([parent, svg, *groups], "card"), {"absent": True})
        self.assertEqual(target_of([parent, *controls], "card"), {"absent": True})

    def test_lock_is_deterministic_and_has_all_slots(self):
        data = evidence([el("h1", box=(0, 0, 100, 40), text="x", font_size="30px")])
        first = targets.lock_targets(data)
        self.assertEqual(list(first), list(targets.SLOTS))
        self.assertEqual(first, targets.lock_targets(data))


class ToleranceAndRounding(unittest.TestCase):
    def test_px_edge_is_inside_and_one_point_one_is_out(self):
        self.assertTrue(score.within("px", 56.0, 57.0))
        self.assertFalse(score.within("px", 56.0, 57.1))

    def test_zero_must_be_zero(self):
        self.assertFalse(score.within("px", 0.0, 0.4))
        self.assertTrue(score.within("px", 0.0, 0.0))

    def test_string_normalised(self):
        self.assertTrue(score.within("string", "Normal", "  normal "))

    def test_relative_two_percent(self):
        self.assertTrue(score.within("rel", 100.0, 101.9))
        self.assertFalse(score.within("rel", 100.0, 102.1))

    def test_threshold_rounds_up_to_whole_pin(self):
        self.assertEqual(score.required_correct(10), 9)
        self.assertEqual(score.required_correct(11), 10)  # 9.9 rounds up to 10
        self.assertEqual(score.required_correct(70), 63)  # 0.9*70 is not exact in floats
        self.assertEqual(score.required_correct(0), 0)

    def test_box_match_is_two_px(self):
        pin = {"x": 10.4, "y": 20, "width": 100, "height": 40}
        self.assertTrue(score.box_matches(pin, [12, 20, 100, 40]))
        self.assertFalse(score.box_matches(pin, [13, 20, 100, 40]))

    def test_placement_matches_app_rule(self):
        vp = {"width": 1440, "height": 900}
        self.assertEqual(score.placement({"x": 0, "y": 0, "width": 10, "height": 10}, vp), "in")
        self.assertEqual(
            score.placement({"x": 0, "y": 900, "width": 10, "height": 10}, vp), "below"
        )
        self.assertEqual(
            score.placement({"x": 1500, "y": 0, "width": 10, "height": 10}, vp), "outside"
        )
        self.assertEqual(
            score.placement({"x": 0, "y": 880, "width": 10, "height": 30}, vp), "partly"
        )
        self.assertEqual(score.placement(None, vp), "none")


class HandValues(unittest.TestCase):
    def test_tracking_converts_through_em(self):
        t = {"style": {"font-size": "20px", "line-height": "24px", "letter-spacing": "-0.4px"}}
        hand = score.hand_values(t)
        self.assertEqual(hand["font_size"], 20.0)
        self.assertEqual(hand["line_height"], 24.0)
        self.assertAlmostEqual(hand["letter_spacing"], -0.4)

    def test_normal_line_height_is_a_string(self):
        t = {"style": {"font-size": "20px", "line-height": "normal", "letter-spacing": "normal"}}
        self.assertEqual(score.hand_values(t)["line_height"], "normal")


class LockAndOrdering(unittest.TestCase):
    def make_run(self, root, pair="C1-01", side="reference", status="ok"):
        d = Path(root) / "run1" / pair / side
        d.mkdir(parents=True)
        (d / "evidence.json").write_text(
            json.dumps(evidence([el("h1", box=(0, 0, 100, 40), text="x", font_size="30px")]))
        )
        return Path(root) / "run1"

    def test_lock_writes_hash_and_refuses_relock(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as root:
            run = self.make_run(root)
            record = targets.lock(root, "run1")
            self.assertEqual(record["targets_sha256"], targets.sha256_file(run / "targets.json"))
            with self.assertRaises(SystemExit):
                targets.lock(root, "run1")

    def test_score_refuses_without_lock(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as root:
            self.make_run(root)
            with self.assertRaises(SystemExit) as ctx:
                score.run_score(root, "run1")
            self.assertIn("not locked", str(ctx.exception))

    def test_score_refuses_when_targets_changed_after_lock(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as root:
            run = self.make_run(root)
            targets.lock(root, "run1")
            (run / "targets.json").write_text("{}")
            with self.assertRaises(SystemExit) as ctx:
                score.run_score(root, "run1")
            self.assertIn("changed", str(ctx.exception))

    def test_fingerprint_is_not_read_by_target_lock(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as root:
            run = self.make_run(root)
            (run / "C1-01" / "reference" / "fingerprint.json").write_text("not json")
            self.assertIn("C1-01", targets.lock(root, "run1")["pairs"])


class PairsFile(unittest.TestCase):
    def test_counts_ids_and_urls(self):
        data = json.loads((REPO / "bench/c1/pairs.json").read_text(encoding="utf-8"))
        self.assertEqual([p["id"] for p in data["primary"]], [f"C1-{n:02d}" for n in range(1, 21)])
        self.assertEqual([r["id"] for r in data["reserves"]], [f"R{n}" for n in range(1, 7)])
        self.assertEqual(data["primary"][0]["reference"], "https://stripe.com/")
        self.assertEqual(data["primary"][0]["build"], "https://linear.app/")
        self.assertEqual(data["primary"][-1]["build"], "https://www.figma.com/")
        self.assertEqual(data["reserves"][-1]["build"], "https://linear.app/")
        for pair in data["primary"] + data["reserves"]:
            self.assertTrue(pair["reference"].startswith("https://"))
            self.assertTrue(pair["reference"].endswith("/") or pair["reference"].endswith("/3/"))
            self.assertTrue(pair["build"].endswith("/") or pair["build"].endswith("/3/"))


class CaptureRecords(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_store_inside_repo_refused(self):
        with self.assertRaises(SystemExit):
            capture.check_store(REPO / "bench")

    def test_timeout_retried_once_then_failure_record(self):
        calls = []

        def fake(url, **_):
            calls.append(url)
            raise MeasureError("measure timed out after 90s")

        side = self.root / "ref"
        state, error = capture.capture_side("https://x.test/", side, measure_fn=fake)
        self.assertEqual((state, len(calls)), ("failed", 2))
        record = json.loads((side / "failure.json").read_text())
        self.assertEqual(record["requested_url"], "https://x.test/")
        self.assertIn("timed out", record["error"])
        self.assertEqual(stat.S_IMODE((side / "failure.json").stat().st_mode), 0o600)

    def test_non_timeout_error_not_retried(self):
        calls = []

        def fake(url, **_):
            calls.append(url)
            raise MeasureError("page answered HTTP 403 from x.test: measure a page that loads")

        state, _ = capture.capture_side("https://x.test/", self.root / "s", measure_fn=fake)
        self.assertEqual((state, len(calls)), ("failed", 1))

    def test_bot_wall_is_a_failure(self):
        def fake(url, *, screenshot, evidence, **_):
            Path(evidence).write_text(
                json.dumps({"page": {"title": "Just a moment...", "text": "Checking your browser"}})
            )
            return {"capture": {"status": 403, "final_url": url}}

        state, error = capture.capture_side("https://x.test/", self.root / "b", measure_fn=fake)
        self.assertEqual(state, "failed")
        self.assertIn("bot wall", error)
        self.assertFalse((self.root / "b" / "fingerprint.json").exists())

    def test_unknown_pair_id_refused(self):
        with self.assertRaises(SystemExit):
            capture.select_pairs(["C1-99"])

    def test_selected_pairs_keep_given_order_and_never_substitute(self):
        picked = capture.select_pairs(["R2", "C1-01"])
        self.assertEqual([p["id"] for p in picked], ["R2", "C1-01"])


class MeasureHookOff(unittest.TestCase):
    def test_fingerprint_unchanged_and_no_cdp_when_off(self):
        import piggyback.measure as m

        class Page:
            def __init__(self):
                self.calls = []

            def evaluate(self, script, *_):
                self.calls.append(("evaluate", script[:20]))
                if "playState" in script:
                    return 0
                return {"anchors": {"heading": None}}

            def screenshot(self, **_):
                self.calls.append(("shot",))

            def emulate_media(self, **_):
                self.calls.append(("emulate",))

            def wait_for_load_state(self, *_, **__):
                pass

            @property
            def context(self):
                raise AssertionError("no CDP session when evidence is off")

        page = Page()
        out = m._capture_motion(page, None)
        self.assertEqual(out["anchors"], {"heading": None})
        self.assertEqual(out["reduced_motion"], {"still_running": 0})
        self.assertNotIn("context", [c[0] for c in page.calls])


class ManifestAndSpotCheck(unittest.TestCase):
    def test_spot_check_is_seeded_and_five_of_pool(self):
        pool = [f"C1-{n:02d}" for n in range(1, 21)]
        a = manifest.spot_check(pool, 7)
        self.assertEqual(a, manifest.spot_check(pool, 7))
        self.assertEqual(len(a), 5)
        self.assertTrue(set(a) <= set(pool))

    def test_dirty_worktree_refused(self):
        with mock.patch.object(manifest, "_git", return_value=" M x"):
            with self.assertRaises(SystemExit):
                manifest.clean_commit()


class SnapshotRead(unittest.TestCase):
    def test_inner_text_skips_hidden_nodes(self):
        data = evidence(
            [
                el("p", box=(0, 0, 300, 20), text="shown"),
                el("span", parent=0, box=None, text="hidden"),
            ]
        )
        snap = Snapshot(data)
        self.assertEqual(snap.inner_text(0), "shown")


class FingerprintContract(unittest.TestCase):
    def test_gap_anchor_slots_match_contract(self):
        self.assertEqual(list(ANCHORS), ["heading", "body", "cta", "nav", "card"])
        self.assertIsNotNone(gap)
        self.assertIsNotNone(fp)
        self.assertEqual(score.SLOT_OF_CONCEPT["heading_type"], "heading")


if __name__ == "__main__":
    with contextlib.redirect_stdout(io.StringIO()):
        unittest.main()
