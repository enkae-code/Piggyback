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


class CountsAndDates(unittest.TestCase):
    def test_nested_matches_are_one_item(self):
        html = '<article class="project"><div class="card"><figure>Only project</figure></div>'
        html += "</article>"
        self.assertFalse(passes("T02", "three selected projects", html))

    def test_wrapper_whose_class_matches_does_not_swallow_its_items(self):
        wrappers = {
            "T02": (
                '<div class="project-list">{}</div>',
                '<article class="project">P</article>',
                3,
            ),
            "T04": ('<div class="card-grid">{}</div>', '<div class="card">P</div>', 3),
            "T08": ('<section class="case-studies">{}</section>', "<article>P</article>", 3),
            "T06": ('<ul class="activity-list">{}</ul>', "<li>P</li>", 3),
            "T09": (
                '<aside class="related"><div class="card-row">{}</div></aside>',
                '<div class="card">P</div>',
                2,
            ),
        }
        items = {"T02": "three selected projects", "T04": "three subscription options"}
        items.update(
            {"T08": "three case studies", "T06": "three activities", "T09": "related cards"}
        )
        for task, (wrapper, leaf, count) in wrappers.items():
            with self.subTest(task=task):
                self.assertTrue(passes(task, items[task], wrapper.format(leaf * count)))

    def test_article_with_heading_and_list_counts_its_items(self):
        html = "<article><h1>Skills swap</h1><ul>"
        html += "".join(f"<li>{name}</li>" for name in ("Bike repair", "Seed swap", "Knitting"))
        html += "</ul></article>"
        self.assertTrue(passes("T06", "three activities", html))

    def test_one_row_tables_do_not_add_up_to_a_table(self):
        html = "".join(f"<table><tr><td>{n}</td></tr></table>" for n in "ABC")
        self.assertFalse(passes("T01", "vessel/asset table", html))
        self.assertFalse(passes("T07", "route table", html))

    def test_rows_of_one_table_meet_the_minimum(self):
        rows = "".join(f"<tr><td>{n}</td></tr>" for n in "ABC")
        self.assertTrue(passes("T07", "route table", f"<table>{rows}</table>"))

    def test_byline_needs_an_author_not_only_a_date(self):
        self.assertFalse(passes("T03", "byline", "<time>12 March 2026</time>"))

    def test_numeric_ratings_are_not_dates(self):
        self.assertFalse(passes("T06", "event details", "<p>Rated 5/5 by readers</p>"))
        html = '<p class="byline">By Ann Lee</p><p>Rated 5/5</p>'
        self.assertFalse(passes("T09", "author/date", html))
        self.assertTrue(passes("T06", "event details", "<p>Saturday 5/5/2026</p>"))

    def test_slash_dates_take_either_order_with_a_year(self):
        for date in ("12/25/2026", "3/14/26", "14/3/2026"):
            with self.subTest(date=date):
                self.assertTrue(passes("T06", "event details", f"<p>{date}</p>"))
        self.assertFalse(passes("T06", "event details", "<p>13/45/2026</p>"))


# Repeated-item probes from the WO-20 round 6 ruling: (task, item, markup, expected pass).
PROBES = [
    (
        "T02",
        "three selected projects",
        '<div class="project-list">' + '<article class="project">P</article>' * 3 + "</div>",
        True,
    ),
    (
        "T04",
        "three subscription options",
        '<div class="card-grid">' + '<div class="card">P</div>' * 3 + "</div>",
        True,
    ),
    (
        "T08",
        "three case studies",
        '<section class="case-studies">' + "<article>P</article>" * 3 + "</section>",
        True,
    ),
    (
        "T06",
        "three activities",
        '<ul class="activity-list">' + "<li>P</li>" * 3 + "</ul>",
        True,
    ),
    (
        "T06",
        "three activities",
        "<article><h1>Skills swap</h1><ul><li>Bike repair</li><li>Seed swap</li>"
        "<li>Knitting</li></ul></article>",
        True,
    ),
    (
        "T09",
        "related cards",
        '<aside class="related"><div class="card-row">'
        + '<div class="card">P</div>' * 2
        + "</div></aside>",
        True,
    ),
    (
        "T04",
        "three subscription options",
        '<div class="card"><div class="card-header">H</div>'
        '<div class="card-body">B</div><div class="card-footer">F</div></div>',
        False,
    ),
    (
        "T04",
        "three subscription options",
        '<div class="card"><div class="card-body">B</div><div class="card-footer">F</div></div>'
        * 2,
        False,
    ),
    (
        "T09",
        "related cards",
        '<aside class="related"><div class="card"><div class="card-title">T</div>'
        '<div class="card-text">X</div></div></aside>',
        False,
    ),
    (
        "T06",
        "three activities",
        '<article class="activity"><ul><li>a</li><li>b</li></ul></article>' * 2,
        False,
    ),
    (
        "T04",
        "three subscription options",
        '<div class="row">' + '<div class="col"><div class="card">P</div></div>' * 3 + "</div>",
        True,
    ),
]


class RepeatedItemProbes(unittest.TestCase):
    def test_probe_table(self):
        for task, item, markup, expected in PROBES:
            with self.subTest(task=task, item=item, markup=markup[:70]):
                self.assertEqual(passes(task, item, markup), expected)


# WO-20 round 8 probes: item suffixes (case-study, card-item), any-word wrappers, and li tags
# scoped to one related card.
ROUND8_PROBES = [
    ("T08", "three case studies", '<div class="case-study">P</div>' * 3, True),
    ("T02", "three selected projects", '<div class="project-item">P</div>' * 3, True),
    ("T04", "three subscription options", '<div class="card-item">P</div>' * 3, True),
    ("T06", "three activities", '<div class="activity-item">P</div>' * 3, True),
    (
        "T09",
        "related cards",
        '<ul class="related-posts">' + "<li>P</li>" * 2 + "</ul>",
        True,
    ),
    (
        "T09",
        "related cards",
        '<section class="related-stories">' + "<article>P</article>" * 2 + "</section>",
        True,
    ),
    (
        "T09",
        "related cards",
        '<div class="related-articles">' + "<li>P</li>" * 2 + "</div>",
        True,
    ),
    (
        "T09",
        "related cards",
        '<aside class="related"><div class="card"><ul><li>a</li><li>b</li><li>c</li></ul></div>'
        "</aside>",
        False,
    ),
    (
        "T09",
        "related cards",
        '<aside class="related"><div class="card"><div class="card-header">H</div>'
        '<div class="card-body">B</div><div class="card-footer">F</div></div></aside>',
        False,
    ),
]


# Related links in page chrome are navigation, not related cards (Opus round-8 LOW).
ROUND8_PROBES += [
    (
        "T09",
        "related cards",
        '<nav class="related-nav"><ul><li>Prev</li><li>Next</li></ul></nav>',
        False,
    ),
    (
        "T09",
        "related cards",
        '<footer><ul class="related-links"><li>a</li><li>b</li></ul></footer>',
        False,
    ),
]


class RoundEightProbes(unittest.TestCase):
    def test_probe_table(self):
        for task, item, markup, expected in ROUND8_PROBES:
            with self.subTest(task=task, item=item, markup=markup[:70]):
                self.assertEqual(passes(task, item, markup), expected)


class SinglePresenceMatching(unittest.TestCase):
    def test_min_one_class_matches_any_word(self):
        for markup in (
            '<div class="signal-strip">Engine due</div>',
            '<div class="alert-box">Engine due</div>',
            '<div class="banner-inner">Engine due</div>',
        ):
            with self.subTest(markup=markup):
                self.assertTrue(passes("T01", "one prominent signal", markup))

    def test_min_one_still_fails_unrelated_markup(self):
        self.assertFalse(
            passes("T01", "one prominent signal", '<div class="showcase">Engine due</div>')
        )


if __name__ == "__main__":
    unittest.main()
