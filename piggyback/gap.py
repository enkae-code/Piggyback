"""Gap between a reference and a build: numbered pins on anchors both pages have.

Page-level comparison only. Pins never claim that one element matches another; each anchor is
compared with itself, and a missing anchor is reported as not found, never guessed.
"""

from dataclasses import dataclass

from . import fingerprint as fp
from .glossary import note

# anchor -> (label, glossary concept): each anchor explains its own type, never another's.
ANCHORS = {
    "heading": ("Heading", "heading_type"),
    "body": ("Body text", "body_type"),
    "cta": ("Primary button", "button_type"),
    "nav": ("Navigation", "nav_type"),
    "card": ("Repeated card", "card_type"),
}
PX_TOLERANCE = 1.0  # the plan's pin gate: a value within 1px is right
SHARE_TOLERANCE = 0.02  # ... and a share within 2% is right
TRACKING_TOLERANCE = 0.01  # em


@dataclass(frozen=True)
class Pin:
    number: int | None  # None for a "not found" entry
    concept: str | None  # None for a "not found" entry: nothing to explain, nothing to compare
    detail: str
    ref_box: dict | None
    build_box: dict | None


def _absent(label, ref_value, build_value):
    """Not-found text naming the side(s) that lack the value. Call only when one side is None."""
    sides = [
        side for side, value in (("reference", ref_value), ("build", build_value)) if value is None
    ]
    return f"{label}: not found on the {' and the '.join(sides)} — can't compare"


def _type_gap(label, ref_anchor, build_anchor, sizes):
    """Pin detail when an anchor's type or tracking differs by more than tolerance, else None.

    The pin reads current build -> reference target, as the plan's examples do.
    """
    rf, bf = sizes
    rl, bl = fp.px(ref_anchor.get("line_height")), fp.px(build_anchor.get("line_height"))
    rt = fp.px(ref_anchor.get("letter_spacing")) or 0.0
    bt = fp.px(build_anchor.get("letter_spacing")) or 0.0
    size_differs = abs(rf - bf) > PX_TOLERANCE
    line_differs = rl is not None and bl is not None and abs(rl - bl) > PX_TOLERANCE
    tracking_differs = abs(rt / rf - bt / bf) > TRACKING_TOLERANCE
    if not (size_differs or line_differs or tracking_differs):
        return None

    def type_text(size, line):
        return f"{fp.fmt(size)}/{'normal' if line is None else fp.fmt(line)}"

    detail = f"{label} {type_text(bf, bl)} → {type_text(rf, rl)}"
    if tracking_differs:
        detail += f", {rt / rf:.2f}em"
    return detail


def _anchor_rows(ref, build):
    """Type gaps on anchors both pages have, and (None, text) not-found entries for the rest."""
    rows, absent = [], []
    for name, (label, concept) in ANCHORS.items():
        ref_anchor, build_anchor = fp.anchor(ref, name), fp.anchor(build, name)
        if ref_anchor is None or build_anchor is None:
            absent.append((None, _absent(label, ref_anchor, build_anchor)))
            continue
        rf, bf = fp.px(ref_anchor.get("font_size")), fp.px(build_anchor.get("font_size"))
        if rf is None or bf is None or rf <= 0 or bf <= 0:
            absent.append((None, f"{label}: font size not measured — can't compare"))
            continue
        detail = _type_gap(label, ref_anchor, build_anchor, (rf, bf))
        if detail:
            boxes = (fp.box(ref_anchor.get("box")), fp.box(build_anchor.get("box")))
            rows.append((concept, detail, *boxes))
    return rows, absent


def _page_rows(ref, build):
    """Accent share and container width, compared at page level, so they carry no boxes."""
    rows, absent = [], []
    ref_share, build_share = (fp.share(fp.get(d, "palette", "accent_share")) for d in (ref, build))
    if ref_share is None or build_share is None:
        absent.append((None, _absent("Accent share", ref_share, build_share)))
    elif abs(round(ref_share - build_share, 4)) > SHARE_TOLERANCE:
        now, target = round(build_share * 100, 1), round(ref_share * 100, 1)
        rows.append(("accent", f"accent on {now:g}% of the page → {target:g}%", None, None))

    ref_width, build_width = (fp.number(fp.get(d, "container_width")) for d in (ref, build))
    if ref_width is None or build_width is None:
        absent.append((None, _absent("Container width", ref_width, build_width)))
    elif abs(ref_width - build_width) > PX_TOLERANCE:
        rows.append(("container", f"{fp.fmt(build_width)}px → {fp.fmt(ref_width)}px", None, None))
    return rows, absent


def gap(ref, build):
    """Pins for the differences between a reference and a build fingerprint."""
    anchor_rows, anchor_absent = _anchor_rows(ref, build)
    page_rows, page_absent = _page_rows(ref, build)
    pins = [Pin(number, *row) for number, row in enumerate(anchor_rows + page_rows, 1)]
    not_found = anchor_absent + page_absent
    return pins + [Pin(None, None, text, None, None) for _, text in not_found]


def _sized(page, name):
    anchor = fp.anchor(page, name)
    size = fp.px(anchor.get("font_size")) if anchor else None
    return size is not None and size > 0


def compared(ref, build):
    """Concepts both pages can be compared on, whether or not they differ."""
    found = {
        concept
        for name, (_, concept) in ANCHORS.items()
        if _sized(ref, name) and _sized(build, name)
    }
    if fp.share(fp.get(ref, "palette", "accent_share")) is not None:
        if fp.share(fp.get(build, "palette", "accent_share")) is not None:
            found.add("accent")
    if fp.number(fp.get(ref, "container_width")) is not None:
        if fp.number(fp.get(build, "container_width")) is not None:
            found.add("container")
    return found


def render(pins):
    """The differences as markdown, numbered to match the pins. Each concept explains once."""
    lines, explained = [], set()
    for pin in pins:
        if pin.number is None:
            lines.append(f"- {pin.detail}")
            continue
        lines.append(note(pin.concept, pin.detail, pin.number, why=pin.concept not in explained))
        explained.add(pin.concept)
    body = "\n".join(lines) or "No differences above the thresholds."
    return f"## Differences (build → reference)\n\n{body}\n"
