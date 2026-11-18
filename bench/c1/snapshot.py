"""Read a saved DOM snapshot (evidence file from piggyback.measure): elements, styles, boxes, text.

Pure data reading, no browser. Boxes are viewport CSS px (the document offset by the scroll position
recorded at capture). Text boxes come from the DOM snapshot's text layout, not from Piggyback.
"""

import re
from dataclasses import dataclass, field

PX = re.compile(r"(-?\d+(?:\.\d+)?)px")


def px(value):
    """CSS px from a computed length, or None for 'normal' and anything else."""
    found = PX.fullmatch(value) if isinstance(value, str) else None
    return float(found.group(1)) if found else None


def letter_px(value):
    """Computed letter-spacing in px; 'normal' is 0. None when the value is not a length."""
    return 0.0 if value == "normal" else px(value)


@dataclass
class El:
    idx: int
    tag: str
    attrs: dict
    parent: int
    children: list
    style: dict | None  # computed styles; None when the element has no layout (not rendered)
    box: tuple | None  # x, y, width, height in viewport px
    boxes: list = field(default_factory=list)  # boxes of the text under this element


class Snapshot:
    def __init__(self, evidence):
        strings = evidence["strings"]
        doc = evidence["documents"][0]
        nodes, layout = doc["nodes"], doc["layout"]
        page = evidence["page"]
        scroll_x, scroll_y = page["scroll"]
        self.viewport = (page["viewport"][0], page["viewport"][1])
        count = len(nodes["nodeType"])
        self.kind = nodes["nodeType"]
        self.parents = nodes["parentIndex"]
        self.children = [[] for _ in range(count)]
        for idx, parent in enumerate(self.parents):
            if parent >= 0:
                self.children[parent].append(idx)

        def text(index):
            return strings[index] if 0 <= index < len(strings) else ""

        values = nodes.get("nodeValue", [-1] * count)
        self.text_value = {i: text(values[i]) for i in range(count) if self.kind[i] == 3}
        layout_of = {node: li for li, node in enumerate(layout["nodeIndex"])}
        styles = evidence["styles"]

        def shifted(bounds):
            x, y, w, h = bounds
            return (x - scroll_x, y - scroll_y, w, h)

        self.els = {}
        for idx in range(count):
            if self.kind[idx] != 1:
                continue
            flat = nodes.get("attributes", [[]] * count)[idx]
            attrs = {text(flat[k]): text(flat[k + 1]) for k in range(0, len(flat) - 1, 2)}
            li = layout_of.get(idx)
            style = box = None
            if li is not None:
                style = dict(zip(styles, (text(v) for v in layout["styles"][li]), strict=True))
                box = shifted(layout["bounds"][li])
            tag = text(nodes["nodeName"][idx]).lower()
            self.els[idx] = El(idx, tag, attrs, self.parents[idx], self.children[idx], style, box)
        boxes = doc.get("textBoxes", {"layoutIndex": [], "bounds": []})
        for k, li in enumerate(boxes["layoutIndex"]):
            node = layout["nodeIndex"][li]
            parent = self.parents[node]
            while parent >= 0:
                if parent in self.els:
                    self.els[parent].boxes.append(shifted(boxes["bounds"][k]))
                parent = self.parents[parent]

    def elements(self):
        return [self.els[idx] for idx in sorted(self.els)]

    def inner_text(self, idx):
        """Approximates innerText: rendered text under idx, whitespace collapsed and trimmed."""
        parts = []
        for child in self.children[idx]:
            if self.kind[child] == 3:
                parts.append(self.text_value[child])
            elif child in self.els and self._rendered(child):
                parts.append(f" {self.inner_text(child)} ")
        return " ".join("".join(parts).split())

    def _rendered(self, idx):
        el = self.els[idx]
        return el.style is not None and el.style.get("visibility") != "hidden"
