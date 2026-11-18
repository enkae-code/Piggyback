"""Packaged starter data must be made from the starter pages now in starters/.

Skips when piggyback/starters/ is absent (nothing is packaged yet). After a starter is remade,
this fails until scripts/regenerate_starters.py runs again. Stale detection is also tested on
fixture folders, so the check itself is proven even while nothing is packaged.
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from piggyback import starters

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "starters"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


LOOPBACK = ("127.0.0.1", "localhost", "[::1]")


def fingerprint_ok(folder, ident):
    """The fingerprint names the starter, never a loopback address."""
    text = (folder / "fingerprint.json").read_text(encoding="utf-8")
    data = json.loads(text)
    label = f"starter:{ident}"
    return (
        data.get("url") == label
        and data.get("capture", {}).get("final_url") == label
        and not any(host in text for host in LOOPBACK)
    )


def stale(packaged, source):
    """Ids of starter pages whose packaged folder is missing, incomplete, or from other HTML,
    and packaged folders with no starter page (orphans)."""
    bad = []
    for folder in sorted(packaged.iterdir()):
        if not (source / folder.name / "index.html").is_file():
            bad.append(folder.name)
    for page in sorted(source.iterdir()):
        if not (page / "index.html").is_file():
            continue
        folder = packaged / page.name
        try:
            recorded = json.loads((folder / "meta.json").read_text(encoding="utf-8"))["sha256"]
            ok = fingerprint_ok(folder, page.name)
        except (OSError, ValueError, KeyError, AttributeError):
            bad.append(page.name)
            continue
        complete = (folder / "fingerprint.json").is_file() and (folder / "screenshot.png").is_file()
        if not complete or not ok or recorded != digest(page / "index.html"):
            bad.append(page.name)
    return bad


class PackagedStarters(unittest.TestCase):
    def test_packaged_data_matches_the_starter_pages(self):
        if not starters.PACKAGED.is_dir():
            self.skipTest("piggyback/starters is not packaged: run scripts/regenerate_starters.py")
        self.assertEqual(stale(starters.PACKAGED, SOURCE), [], "run scripts/regenerate_starters.py")


class StaleDetection(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.source = Path(tmp.name) / "starters"
        self.packaged = Path(tmp.name) / "piggyback-starters"
        page = self.source / "page"
        page.mkdir(parents=True)
        (page / "index.html").write_text("first version", encoding="utf-8")
        folder = self.packaged / "page"
        folder.mkdir(parents=True)
        self.fingerprint = {"url": "starter:page", "capture": {"final_url": "starter:page"}}
        (folder / "fingerprint.json").write_text(json.dumps(self.fingerprint), encoding="utf-8")
        (folder / "screenshot.png").write_bytes(b"png")
        meta = {"sha256": digest(page / "index.html")}
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def write_fingerprint(self, data):
        (self.packaged / "page" / "fingerprint.json").write_text(json.dumps(data), encoding="utf-8")

    def test_matching_packaged_data_is_not_stale(self):
        self.assertEqual(stale(self.packaged, self.source), [])

    def test_a_remade_page_makes_its_packaged_data_stale(self):
        (self.source / "page" / "index.html").write_text("second version", encoding="utf-8")
        self.assertEqual(stale(self.packaged, self.source), ["page"])

    def test_missing_packaged_data_is_stale(self):
        (self.packaged / "page" / "screenshot.png").unlink()
        self.assertEqual(stale(self.packaged, self.source), ["page"])

    def test_meta_without_fingerprint_or_screenshot_is_stale(self):
        (self.packaged / "page" / "fingerprint.json").unlink()
        self.assertEqual(stale(self.packaged, self.source), ["page"])

    def test_an_orphan_packaged_folder_is_stale(self):
        (self.packaged / "ghost").mkdir()
        self.assertEqual(stale(self.packaged, self.source), ["ghost"])

    def test_a_fingerprint_that_is_not_exactly_the_starter_label_is_stale(self):
        for name, data in [
            ("other url", {"url": "starter:other", "capture": {"final_url": "starter:page"}}),
            ("other final url", {"url": "starter:page", "capture": {"final_url": "https://x/"}}),
            ("loopback text", {**self.fingerprint, "anchors": [{"href": "http://localhost:9/"}]}),
            ("ipv6 loopback", {**self.fingerprint, "note": "http://[::1]:9/"}),
        ]:
            with self.subTest(name):
                self.write_fingerprint(data)
                self.assertEqual(stale(self.packaged, self.source), ["page"])


if __name__ == "__main__":
    unittest.main()
