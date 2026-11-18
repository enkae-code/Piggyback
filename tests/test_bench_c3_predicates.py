"""C3 content predicates, pure DOM: a positive and a negative case for every item of all ten rows.

The HTML is parsed into the same snapshot shape the browser builds (snapshot.js in checklist.py),
so these cases run without Playwright. The negative cases kill the mutations seen in review:
a loosened T02 minimum, a loosened T06 event pattern, and a T09 CTA that is not a membership CTA.
"""

import json
import unittest
from html.parser import HTMLParser

from bench.c3 import checklist, predicates

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
INVISIBLE = {"head", "script", "style", "title", "meta", "link", "template"}


class DomParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.open = []
        self.body = []
        self.controls = []
        self.control = None

    def _visible_here(self, tag, attrs):
        if "hidden" in attrs or "display:none" in attrs.get("style", "").replace(" ", ""):
            return False
        if tag in INVISIBLE:
            return False
        return not self.open or self.nodes[self.open[-1]]["visible"]

    def handle_starttag(self, tag, attrs):
        self.body.append(" ")  # innerText puts a line break between blocks
        attrs = {k: v or "" for k, v in attrs}
        index = len(self.nodes)
        node = {
            "tag": tag,
            "attrs": attrs,
            "visible": self._visible_here(tag, attrs),
            "path": list(self.open),
            "parts": [],
        }
        self.nodes.append(node)
        if tag in ("a", "button"):
            self.control = {"index": index, "aria": attrs.get("aria-label"), "parts": []}
        if tag == "input" and attrs.get("type") in ("button", "submit"):
            if node["visible"]:
                self.controls.append(attrs.get("value", ""))
        if tag not in VOID:
            self.open.append(index)

    def handle_endtag(self, tag):
        self.body.append(" ")
        for position in range(len(self.open) - 1, -1, -1):
            if self.nodes[self.open[position]]["tag"] == tag:
                del self.open[position:]
                break
        if tag in ("a", "button") and self.control:
            control, self.control = self.control, None
            if self.nodes[control["index"]]["visible"]:
                name = control["aria"] or "".join(control["parts"])
                self.controls.append(" ".join(name.split()))

    def handle_data(self, data):
        for index in self.open:
            self.nodes[index]["parts"].append(data)
        if self.control:
            self.control["parts"].append(data)
        if not self.open or self.nodes[self.open[-1]]["visible"]:
            self.body.append(data)


def snapshot(html):
    parser = DomParser()
    parser.feed(html)
    nodes = [
        {
            "tag": n["tag"],
            "attrs": n["attrs"],
            "visible": n["visible"],
            "path": n["path"],
            "text": " ".join("".join(n["parts"]).split()),
        }
        for n in parser.nodes
    ]
    return {
        "text": " ".join("".join(parser.body).split()),
        "nodes": nodes,
        "controls": parser.controls,
    }


def predicate(task, item):
    preds = checklist.predicates_for(task)
    return next(p for p in preds if p["item"] == item)


def passes(task, item, html):
    return predicates.passes(predicate(task, item), snapshot(html))


# (task, item, positive fragments, negative fragments). Each fragment is a whole page body.
CASES = [
    ("T01", "status summary", ["<h2>Status summary</h2>"], ["<h2>Vessels</h2>"]),
    (
        "T01",
        "one prominent signal",
        ['<div role="alert">Engine due</div>', '<div class="signal-strip">Engine due</div>'],
        ['<div class="showcase">Engine due</div>'],
    ),
    (
        "T01",
        "vessel/asset table",
        ["<table><tr><td>A</td></tr><tr><td>B</td></tr><tr><td>C</td></tr></table>"],
        [
            "<table><tr><td>A</td></tr><tr><td>B</td></tr></table>",
            "<table><tr><th>A</th></tr><tr><th>B</th></tr><tr><th>C</th></tr></table>",
        ],
    ),
    ("T01", "acknowledge CTA", ["<button>Acknowledge alert</button>"], ["<button>Close</button>"]),
    ("T02", "concise introduction", ["<p>I design books.</p>"], ["<h2>Hello</h2>"]),
    (
        "T02",
        "three selected projects",
        ["<article>a</article><article>b</article><article>c</article>"],
        [
            "<article>a</article><article>b</article>",
            '<div class="showcase">a</div><div class="showcase">b</div>'
            '<div class="showcase">c</div>',
        ],
    ),
    ("T02", "contact CTA", ["<a href='#'>Get in touch</a>"], ["<a href='#'>Read more</a>"]),
    ("T03", "feature headline", ["<h1>Lead story</h1>"], ["<p>Lead story</p>"]),
    (
        "T03",
        "standfirst",
        [
            "<header><h1>Lead story</h1><p>A deck that says more.</p></header><p>Body opening.</p>",
            '<main><h1>Lead story</h1><div class="standfirst">A deck.</div><p>Body.</p></main>',
        ],
        [
            "<header><h1>Lead story</h1></header><h2>Section</h2><p>Body.</p>",
            '<h1>Lead story</h1><p class="byline">Jane Doe</p>',
        ],
    ),
    (
        "T03",
        "byline",
        ["<address>Jane Doe</address>", "<p>By Jane Doe</p>", '<p class="byline">Jane</p>'],
        ["<p>Posted by the editors</p>", "<p>By the way</p>"],
    ),
    (
        "T03",
        "article opening",
        ["<p>One.</p><p>Two.</p>"],
        ["<p>One.</p>"],
    ),
    ("T03", "related stories", ["<h2>Related stories</h2>"], ["<h2>More reading</h2>"]),
    ("T03", "subscribe CTA", ["<button>Subscribe</button>"], ["<button>Share</button>"]),
    (
        "T04",
        "three subscription options",
        ["<article>a</article><article>b</article><article>c</article>"],
        ["<article>a</article><article>b</article>"],
    ),
    (
        "T04",
        "clear differences",
        ["<ul><li>Print</li><li>Digital</li><li>Both</li></ul>"],
        [
            "<nav><ul><li>Home</li><li>Shop</li><li>About</li></ul></nav><p>x</p>",
            "<ul><li>Print</li><li>Digital</li></ul>",
        ],
    ),
    ("T04", "one subscribe CTA", ["<button>Subscribe</button>"], ["<button>Buy</button>"]),
    ("T05", "seasonal produce", ["<h2>Seasonal produce</h2>"], ["<h2>Our story</h2>"]),
    (
        "T05",
        "growing approach",
        ["<p>Our growing approach is no-dig.</p>"],
        ["<p>We are open daily.</p>"],
    ),
    (
        "T05",
        "weekly market details",
        ["<p>Weekly market every Saturday.</p>"],
        ["<p>We are open daily.</p>"],
    ),
    (
        "T05",
        "visit/order CTA",
        ["<a href='#'>Order a box</a>"],
        ["<a href='#'>Menu</a>"],
    ),
    (
        "T06",
        "event details",
        [
            "<p>Saturday 10 May, 10:30 am</p>",
            "<p>Jun 14 at the hall</p>",
            "<p>14th March</p>",
            "<p>2026-05-10</p>",
            "<p>Doors at 6 p.m.</p>",
            "<p>Starts 18:30</p>",
            "<p>Saturday</p>",
        ],
        [
            "<p>Come along and learn something new.</p>",
            "<p>Version 2 of the plan</p>",
            "<p>We may be late</p>",
        ],
    ),
    (
        "T06",
        "three activities",
        ["<ul><li>Bike repair</li><li>Seed swap</li><li>Knitting</li></ul>"],
        [
            "<nav><ul><li>Home</li><li>Events</li><li>Contact</li></ul></nav><p>x</p>",
            "<ul><li>Bike repair</li><li>Seed swap</li></ul>",
        ],
    ),
    (
        "T06",
        "access information",
        ["<p>Step-free entrance on Mill Road.</p>"],
        ["<p>Parking is nearby.</p>"],
    ),
    (
        "T06",
        "save-your-place CTA",
        ["<button>Save your place</button>"],
        ["<button>Share</button>"],
    ),
    ("T07", "service status", ["<h2>Service status</h2>"], ["<h2>Timetable</h2>"]),
    ("T07", "urgent alert", ["<p>Urgent: line 4 is closed.</p>"], ["<p>Line 4 is closed.</p>"]),
    (
        "T07",
        "route table",
        ["<table><tr><td>1</td></tr><tr><td>2</td></tr><tr><td>3</td></tr></table>"],
        ["<table><tr><td>1</td></tr><tr><td>2</td></tr></table>"],
    ),
    (
        "T07",
        "report-issue CTA",
        ["<button>Report an issue</button>"],
        ["<button>Contact us</button>"],
    ),
    ("T08", "introduction", ["<p>I test accessibility.</p>"], ["<h1>Hi</h1>"]),
    (
        "T08",
        "three case studies",
        ["<article>a</article><article>b</article><article>c</article>"],
        [
            '<div class="showcase">a</div><div class="showcase">b</div>'
            '<div class="showcase">c</div>',
            "<article>a</article><article>b</article>",
        ],
    ),
    ("T08", "inquiry CTA", ["<a href='#'>Inquire</a>"], ["<a href='#'>Read case study</a>"]),
    ("T09", "headline", ["<h1>Story</h1>"], ["<h2>Story</h2>"]),
    (
        "T09",
        "deck",
        [
            "<header><h1>Story</h1><p>Deck text.</p></header><p>Body.</p>",
            '<h1>Story</h1><div class="deck">Deck text.</div>',
        ],
        ["<header><h1>Story</h1></header><h2>S</h2><p>Body.</p>"],
    ),
    (
        "T09",
        "author/date",
        [
            '<p class="byline">By Ann Lee</p><time>12 March 2026</time>',
            "<address>Ann Lee</address><p>12 March 2026</p>",
        ],
        [
            '<p class="byline">By Ann Lee</p>',
            "<time>12 March 2026</time>",
            "<p>Posted by the editors on March 12</p>",
        ],
    ),
    ("T09", "body opening", ["<p>One.</p><p>Two.</p>"], ["<p>One.</p>"]),
    (
        "T09",
        "related cards",
        ['<div class="card">a</div><div class="card">b</div>'],
        ['<div class="card">a</div>'],
    ),
    (
        "T09",
        "membership CTA",
        ["<button>Become a member</button>"],
        ["<button>Read more</button>"],
    ),
    (
        "T10",
        "mission",
        ["<h2>Our mission</h2><p>Grow food together.</p>", "<p>Our purpose: feed the street.</p>"],
        [
            "<h2>Schedule</h2><p>Mon 9 am.</p>",
            "<h2>Our mission</h2><p>" + "word " * 70 + "</p>",
        ],
    ),
    (
        "T10",
        "volunteer schedule",
        ["<p>Shifts run Saturday 9:00 am.</p>"],
        ["<p>Come say hello.</p>"],
    ),
    (
        "T10",
        "produce share",
        ["<p>Produce share every Friday.</p>"],
        ["<p>Plant seeds here.</p>"],
    ),
    ("T10", "volunteer CTA", ["<button>Get involved</button>"], ["<button>Donate</button>"]),
]


class ContentPredicates(unittest.TestCase):
    def test_every_content_item_has_a_positive_and_a_negative_case(self):
        covered = {(task, item) for task, item, _, _ in CASES}
        tasks = json.loads(checklist.TASKS_PATH.read_text(encoding="utf-8"))["tasks"]
        wanted = {(task["id"], pred["item"]) for task in tasks for pred in task["predicates"]}
        self.assertEqual(covered, wanted)
        self.assertEqual(len(CASES), 41)

    def test_positive_fragments_pass(self):
        for task, item, positives, _ in CASES:
            for html in positives:
                with self.subTest(task=task, item=item, html=html):
                    self.assertTrue(passes(task, item, html))

    def test_negative_fragments_fail(self):
        for task, item, _, negatives in CASES:
            for html in negatives:
                with self.subTest(task=task, item=item, html=html[:60]):
                    self.assertFalse(passes(task, item, html))

    def test_event_date_accepts_each_frozen_form(self):
        for form in ["Sat 10:30 a.m.", "May 10th", "10 May", "2026-05-10", "18:30"]:
            with self.subTest(form=form):
                self.assertTrue(passes("T06", "event details", f"<p>{form}</p>"))

    def test_word_boundary_rejects_substring_hits(self):
        self.assertFalse(passes("T01", "one prominent signal", '<div class="showcase">x</div>'))
        self.assertFalse(passes("T01", "one prominent signal", '<div class="uppercase">x</div>'))
        self.assertFalse(passes("T09", "related cards", '<div class="showcase">a</div>'))

    def test_hidden_items_do_not_count(self):
        html = "<table><tr><td>1</td></tr><tr><td>2</td></tr><tr hidden><td>3</td></tr></table>"
        self.assertFalse(passes("T07", "route table", html))

    def test_nav_list_does_not_count_as_differences(self):
        html = "<nav><ul><li>a</li><li>b</li><li>c</li></ul></nav>"
        self.assertFalse(passes("T04", "clear differences", html))

    def test_failed_item_message_carries_the_rule(self):
        checks = {
            "render": {"pass": True, "page_errors": [], "console_errors": []},
            "content": [
                predicates.evaluate_predicate(
                    predicate("T03", "standfirst"), snapshot("<h1>Lead</h1><h2>x</h2>")
                )
            ],
            "overflow": [],
            "axe": {"error": None, "violations": []},
        }
        self.assertEqual(
            checklist.failed_items(checks),
            ["content item missing: standfirst (a paragraph directly after the main headline)"],
        )


if __name__ == "__main__":
    unittest.main()
