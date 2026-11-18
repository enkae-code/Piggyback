"""C3 checklist and prompt assembly, without a browser. Browser checks: test_bench_c3_browser.py."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from bench.c3 import checklist, prompts

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "c3" / "spec_text.json"
AXE = ROOT / "bench" / "c3" / "vendor" / "axe-core" / "axe.min.js"
README = ROOT / "bench" / "c3" / "README.md"
ARM_IDS = ["piggyback-brief", "screenshot", "hand-design-md", "skill-alone", "skill-plus-piggyback"]


def spec_text():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class SpecFreeze(unittest.TestCase):
    def test_tasks_are_the_frozen_ten_rows(self):
        tasks = json.loads(checklist.TASKS_PATH.read_text(encoding="utf-8"))["tasks"]
        frozen = spec_text()["tasks"]
        self.assertEqual([t["id"] for t in tasks], [f"T{n:02d}" for n in range(1, 11)])
        for task in tasks:
            self.assertEqual(task["build_request"], frozen[task["id"]]["build_request"])
            self.assertEqual(task["reference"], frozen[task["id"]]["reference"])
            self.assertTrue(task["predicates"], task["id"])

    def test_one_predicate_per_content_item_in_each_row(self):
        # Content items named in each task row, in the order the row lists them.
        items = {
            "T01": 4, "T02": 3, "T03": 6, "T04": 3, "T05": 4,
            "T06": 4, "T07": 4, "T08": 3, "T09": 6, "T10": 4,
        }  # fmt: skip
        tasks = json.loads(checklist.TASKS_PATH.read_text(encoding="utf-8"))["tasks"]
        self.assertEqual({t["id"]: len(t["predicates"]) for t in tasks}, items)

    def test_prompt_strings_are_verbatim_spec(self):
        frozen = spec_text()
        self.assertEqual(prompts.SHARED_INSTRUCTION, frozen["shared_instruction"])
        self.assertEqual(prompts.ARM_BLOCKS, frozen["arms"])

    def test_expanded_prompt_has_one_arm_block_and_the_placeholder(self):
        frozen = spec_text()
        task = prompts.load_tasks()[0]
        for arm in ARM_IDS:
            text = prompts.expand(task, arm)
            self.assertIn(frozen["shared_instruction"], text)
            self.assertIn(task["build_request"], text)
            self.assertIn(frozen["arms"][arm], text)
            self.assertEqual(text.count(prompts.RUNTIME_PLACEHOLDER), 1)
            for other in ARM_IDS:
                if other != arm:
                    self.assertNotIn(frozen["arms"][other], text)

    def test_write_all_writes_fifty_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = prompts.write_all(tmp)
            self.assertEqual(len(written), 50)
            self.assertEqual(len(list(Path(tmp).glob("*.txt"))), 50)

    def test_axe_hash_matches_readme(self):
        digest = hashlib.sha256(AXE.read_bytes()).hexdigest()
        self.assertIn(digest, README.read_text(encoding="utf-8"))


class Predicates(unittest.TestCase):
    def snap(self, text="", counts=None, controls=()):
        return {"text": text, "counts": counts or {}, "controls": list(controls)}

    def test_text_is_case_insensitive_search(self):
        pred = {"item": "x", "kind": "text", "pattern": "status|summary"}
        self.assertTrue(checklist.evaluate_predicate(pred, self.snap("Status Board"))["pass"])
        self.assertFalse(checklist.evaluate_predicate(pred, self.snap("hello"))["pass"])

    def test_count_needs_min(self):
        pred = {"item": "x", "kind": "count", "selector": "tr", "min": 3}
        self.assertTrue(checklist.evaluate_predicate(pred, self.snap(counts={"tr": 3}))["pass"])
        self.assertFalse(checklist.evaluate_predicate(pred, self.snap(counts={"tr": 2}))["pass"])

    def test_control_name_matches_an_accessible_name(self):
        pred = {"item": "x", "kind": "control_name", "pattern": "acknowledg"}
        ok = self.snap(controls=["Acknowledge alert"])
        self.assertTrue(checklist.evaluate_predicate(pred, ok)["pass"])
        self.assertFalse(checklist.evaluate_predicate(pred, self.snap(controls=["Close"]))["pass"])

    def test_unknown_kind_is_an_error(self):
        with self.assertRaises(ValueError):
            checklist.evaluate_predicate({"item": "x", "kind": "vibe"}, self.snap())


class Checks(unittest.TestCase):
    def test_overflow_is_strictly_wider_than_viewport(self):
        self.assertTrue(checklist.has_horizontal_overflow(1441, 1440))
        self.assertFalse(checklist.has_horizontal_overflow(1440, 1440))

    def checks(self, **over):
        base = {
            "render": {"pass": True},
            "content": [],
            "overflow": [{"width": 1440, "pass": True}, {"width": 390, "pass": True}],
            "axe": {"error": None, "violations": [], "incomplete": [{"id": "x"}], "pass": True},
        }
        base.update(over)
        return base

    def test_passing_page_has_no_items_and_no_message(self):
        failed = checklist.failed_items(self.checks())
        self.assertEqual(failed, [])
        self.assertIsNone(checklist.correction_message(failed))

    def test_incomplete_results_never_fail(self):
        self.assertEqual(checklist.failed_items(self.checks()), [])

    def test_each_failed_check_adds_one_verbatim_item(self):
        checks = self.checks(
            render={"pass": False},
            content=[{"item": "vessel/asset table", "pass": False}],
            overflow=[{"width": 1440, "pass": False}, {"width": 390, "pass": True}],
            axe={
                "error": None,
                "violations": [{"id": "image-alt", "help": "Images must have alternate text"}],
                "incomplete": [],
                "pass": False,
            },
        )
        self.assertEqual(
            checklist.failed_items(checks),
            [
                checklist.RENDER_ITEM,
                "content item missing: vessel/asset table",
                "horizontal overflow at 1440 px",
                "axe-core violation image-alt: Images must have alternate text",
            ],
        )

    def test_axe_error_fails_closed(self):
        checks = self.checks(
            axe={"error": "no axe", "violations": [], "incomplete": [], "pass": False}
        )
        self.assertEqual(checklist.failed_items(checks), ["axe-core could not run: no axe"])

    def test_message_template_lists_items_verbatim(self):
        message = checklist.correction_message(["a item", "b item"])
        self.assertEqual(
            message,
            "The page failed these checklist items:\n- a item\n- b item\n"
            "Fix each listed item and return the complete page.",
        )


class TargetPolicy(unittest.TestCase):
    def test_loopback_and_file_targets_are_allowed(self):
        self.assertEqual(checklist.target_url("http://127.0.0.1:8123/a"), "http://127.0.0.1:8123/a")
        self.assertEqual(checklist.target_url("http://localhost:8123/"), "http://127.0.0.1:8123/")
        with tempfile.NamedTemporaryFile(suffix=".html") as page:
            self.assertTrue(checklist.target_url(page.name).startswith("file://"))

    def test_public_or_private_targets_are_refused(self):
        for target in [
            "https://example.com/",
            "http://10.0.0.1/",
            "http://printer.local/",
            "http://app.localhost:3000/",
            "missing-file.html",
            "ftp://127.0.0.1/",
        ]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                checklist.target_url(target)

    def test_page_requests_are_limited_to_loopback_and_local_schemes(self):
        self.assertTrue(checklist.allowed_request("file:///x.html"))
        self.assertTrue(checklist.allowed_request("data:text/plain,a"))
        self.assertTrue(checklist.allowed_request("http://127.0.0.1:9/x.js"))
        self.assertFalse(checklist.allowed_request("https://cdn.example.com/x.js"))
        self.assertFalse(checklist.allowed_request("http://192.168.1.2/"))

    def test_unknown_task_is_an_error(self):
        with self.assertRaises(ValueError):
            checklist.predicates_for("T99")


if __name__ == "__main__":
    unittest.main()
