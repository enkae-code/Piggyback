"""scripts/regenerate_starters.py without a browser: the loopback relabelling and the name table."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "regenerate_starters.py"


def load_script():
    spec = importlib.util.spec_from_file_location("regenerate_starters", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Relabel(unittest.TestCase):
    def test_every_loopback_page_address_becomes_the_starter_label(self):
        script = load_script()
        base = "http://127.0.0.1:5050/northline-ops/index.html"
        data = {
            "url": base,
            "capture": {"final_url": base, "status": 200},
            "anchors": [{"href": base}],
            "viewport": {"width": 1440},
        }
        out = script.relabel(data, base, "starter:northline-ops")
        self.assertEqual(out["url"], "starter:northline-ops")
        self.assertEqual(out["capture"], {"final_url": "starter:northline-ops", "status": 200})
        self.assertEqual(out["anchors"], [{"href": "starter:northline-ops"}])
        self.assertEqual(out["viewport"], {"width": 1440})
        self.assertNotIn("127.0.0.1", repr(out))


class Names(unittest.TestCase):
    def test_the_name_table_covers_exactly_the_starter_pages(self):
        script = load_script()
        pages = {
            path.name for path in (ROOT / "starters").iterdir() if (path / "index.html").is_file()
        }
        self.assertEqual(set(script.NAMES), pages)


if __name__ == "__main__":
    unittest.main()
