"""Content predicates of the C3 checklist, evaluated on a DOM snapshot.

A snapshot is {"text": rendered body text, "nodes": [...], "controls": [accessible names]}.
Each node is {"tag", "attrs", "visible", "path", "text"}. "path" holds the indices of the node's
ancestors, so structure and document order need no selector engine. The browser builds this
shape in snapshot.js; the tests build it from HTML with the same meaning.
"""

import re
from collections import Counter

HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
SHORT_HEADINGS = ("h1", "h2", "h3", "h4")
CONTAINERS = ("header", "article", "main")


def class_words(attrs):
    """Words of the class attribute, split on '-' and '_' so 'plan-card' has 'plan'."""
    words = set()
    for token in attrs.get("class", "").lower().split():
        words.update(re.split(r"[-_]", token))
    return words


def class_names(attrs):
    """Last word of each class token, so 'plan-card' is a card but 'card-body' is not."""
    return {re.split(r"[-_]", token)[-1] for token in attrs.get("class", "").lower().split()}


def _attr_matches(spec, attrs):
    name, _, value = spec.partition("=")
    if name not in attrs:
        return False
    return not value or attrs[name].strip().lower() == value


def _has_visible_child(tag, index, nodes):
    return any(
        other["tag"] == tag and other["visible"] and index in other["path"] for other in nodes
    )


def _words(attrs, strict):
    """Class words to match against: the last word of each token when strict, any word otherwise."""
    return class_names(attrs) if strict else class_words(attrs)


def _in_class(alt, node, nodes, strict):
    return any(alt["in_class"] in _words(nodes[j]["attrs"], strict) for j in node["path"])


# Each check reads one key of an alternative. An alternative matches when every key it sets holds.
_ALT_CHECKS = {
    "tag": lambda alt, node, index, nodes, strict: alt["tag"] == node["tag"],
    "class": lambda alt, node, index, nodes, strict: alt["class"] in _words(node["attrs"], strict),
    "attr": lambda alt, node, index, nodes, strict: _attr_matches(alt["attr"], node["attrs"]),
    "in_class": lambda alt, node, index, nodes, strict: _in_class(alt, node, nodes, strict),
    "has": lambda alt, node, index, nodes, strict: _has_visible_child(alt["has"], index, nodes),
}


def _alt_matches(alt, index, nodes, strict):
    node = nodes[index]
    return all(
        check(alt, node, index, nodes, strict) for key, check in _ALT_CHECKS.items() if key in alt
    )


def _nearest_table(nodes, path):
    return next((j for j in reversed(path) if nodes[j]["tag"] == "table"), None)


def _is_tag_alt(alt):
    return "tag" in alt and not {"class", "in_class", "attr"} & alt.keys()


# ponytail: a bare tag item (<article> with no class) holding several tag-matched children
# (3 figures) still over-counts; add a class to the item when a task needs the exact count.
def _matched_items(pred, nodes, strict):
    """Visible elements matching an alternative, outside the excluded ancestors.

    Strict (repeated items): a tag-only match inside a class-matched element is that item's content,
    not another item. Otherwise every match counts, which is enough for a presence check.
    """
    excluded = pred.get("exclude_within", [])
    eligible = [
        index
        for index, node in enumerate(nodes)
        if node["visible"] and not any(nodes[j]["tag"] in excluded for j in node["path"])
    ]
    if not strict:
        return {
            i for i in eligible if any(_alt_matches(alt, i, nodes, False) for alt in pred["any"])
        }
    by_class = {
        i
        for i in eligible
        if any(not _is_tag_alt(alt) and _alt_matches(alt, i, nodes, True) for alt in pred["any"])
    }
    by_tag = {i for i in eligible if any(_alt_matches(alt, i, nodes, True) for alt in pred["any"])}
    return {i for i in by_tag if i in by_class or not any(j in by_class for j in nodes[i]["path"])}


def count_matches(pred, nodes):
    """Items matching any alternative. Repeated items (min 2 or more) count by last class word,
    keep only innermost matches, and drop tag matches inside a class match. A presence check (min 1)
    counts any match, with any class word. With per_table, the count is the most matches held by
    one table: rows must share a table.
    """
    strict = pred.get("min", 1) >= 2
    matched = _matched_items(pred, nodes, strict)
    if pred.get("per_table"):
        groups = Counter(_nearest_table(nodes, nodes[i]["path"]) for i in matched)
        groups.pop(None, None)
        return max(groups.values(), default=0)
    if not strict:
        return len(matched)
    return sum(1 for i in matched if not any(i in nodes[k]["path"] for k in matched))


def _scope_of_headline(nodes, first):
    return next((j for j in reversed(nodes[first]["path"]) if nodes[j]["tag"] in CONTAINERS), None)


def _section_after(nodes, first, scope):
    """Indices after the headline, inside its scope, up to the next visible h2 to h6 heading."""
    for index in range(first + 1, len(nodes)):
        node = nodes[index]
        if scope is not None and scope not in node["path"]:
            return
        if node["visible"] and node["tag"] in HEADINGS[1:]:
            return
        yield index


def follows_headline(pred, nodes):
    """True when a qualifying element follows the first visible h1 inside its header, article or
    main, before any h2 to h6 heading. Byline-like elements are not the deck."""
    first = next((i for i, n in enumerate(nodes) if n["tag"] == "h1" and n["visible"]), None)
    if first is None:
        return False
    excluded = set(pred.get("exclude_class", []))
    section = _section_after(nodes, first, _scope_of_headline(nodes, first))
    return any(
        nodes[i]["visible"]
        and not class_words(nodes[i]["attrs"]) & excluded
        and any(_alt_matches(alt, i, nodes, False) for alt in pred["any"])
        for i in section
    )


def heading_statement(pred, nodes):
    """A short paragraph (first visible p) under a visible heading that matches pred['heading']."""
    for index, node in enumerate(nodes):
        if not node["visible"] or node["tag"] not in SHORT_HEADINGS:
            continue
        if not re.search(pred["heading"], node["text"], re.IGNORECASE):
            continue
        for other in nodes[index + 1 :]:
            if other["visible"] and other["tag"] in HEADINGS:
                break
            if other["visible"] and other["tag"] == "p":
                words = len(other["text"].split())
                return 1 <= words <= pred.get("max_words", 60)
    return False


def _text_passes(pred, snap):
    flags = 0 if pred.get("case_sensitive") else re.IGNORECASE
    return re.search(pred["pattern"], snap.get("text", ""), flags) is not None


def _control_passes(pred, snap):
    return any(re.search(pred["pattern"], name, re.IGNORECASE) for name in snap.get("controls", []))


_KINDS = {
    "text": _text_passes,
    "control_name": _control_passes,
    "count": lambda pred, snap: count_matches(pred, snap.get("nodes", [])) >= pred["min"],
    "follows_headline": lambda pred, snap: follows_headline(pred, snap.get("nodes", [])),
    "heading_statement": lambda pred, snap: heading_statement(pred, snap.get("nodes", [])),
    "any": lambda pred, snap: any(passes(sub, snap) for sub in pred["of"]),
    "all": lambda pred, snap: all(passes(sub, snap) for sub in pred["of"]),
}


def passes(pred, snap):
    kind = pred["kind"]
    if kind not in _KINDS:
        raise ValueError(f"unknown predicate kind: {kind}")
    return _KINDS[kind](pred, snap)


def evaluate_predicate(pred, snap):
    return {
        "item": pred["item"],
        "kind": pred["kind"],
        "rule": pred["rule"],
        "pass": passes(pred, snap),
    }
