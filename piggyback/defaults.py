"""Defaults detected on one build fingerprint. A rule fires only when its evidence is present."""

from . import fingerprint as fp
from .glossary import note

FLAT_RATIO = 1.5  # headings at most 1.5x the body text size read as a flat scale
MIN_REPEATS = 3  # the same fade-and-move on at least this many elements


def find(build):
    """[(concept, detail)] for each generic default the build fingerprint shows."""
    rules = (_gradient_text, _one_font_flat_scale, _uniform_radius, _repeated_motion, _icon_tile)
    return [hit for hit in (rule(build) for rule in rules) if hit]


def render(hits):
    if not hits:
        return "No generic defaults detected.\n"
    lines = [note(concept, detail, number) for number, (concept, detail) in enumerate(hits, 1)]
    return "## Defaults detected\n\n" + "\n".join(lines) + "\n"


def _gradient_text(build):
    count = fp.number(fp.get(build, "effects", "gradient_text"))
    if count is None or count < 1:
        return None
    return "gradient_text", f"{count:g} text element(s) filled with a gradient"


def _one_font_flat_scale(build):
    # The first name in each family list is the primary font; generic fallbacks are not counted.
    primary = {
        name
        for value, _ in fp.pairs(fp.get(build, "type", "families"))
        if (name := fp.primary_font(value))
    }
    heading, body = fp.anchor(build, "heading"), fp.anchor(build, "body")
    if heading is None or body is None or len(primary) != 1:
        return None
    heading_px, body_px = fp.px(heading.get("font_size")), fp.px(body.get("font_size"))
    if not heading_px or not body_px:
        return None
    ratio = heading_px / body_px
    if ratio > FLAT_RATIO:
        return None
    return "one_font_flat_scale", f"one font ({primary.pop()}) and headings {ratio:.1f}x body text"


def _uniform_radius(build):
    sizes = {fp.px(value) for value, _ in fp.pairs(fp.get(build, "radius"))} - {None}
    if len(sizes) != 1:
        return None
    return "uniform_radius", f"one radius ({next(iter(sizes)):g}px) on every rounded box"


def _repeated_motion(build):
    rows = fp.get(build, "motion")
    for row in rows if isinstance(rows, list) else []:
        props = fp.get(row, "props")
        count = fp.number(fp.get(row, "count"))
        duration = fp.number(fp.get(row, "duration"))
        if not _names(props) or count is None or duration is None:
            continue
        if {"opacity", "transform"} <= set(props) and count >= MIN_REPEATS:
            return (
                "repeated_motion",
                f"opacity and transform on {count:g} elements over {duration:g}ms",
            )
    return None


def _names(props):
    """Property names from a motion row: a list of strings, or None for anything else."""
    if isinstance(props, list) and all(isinstance(prop, str) for prop in props):
        return props
    return None


def _icon_tile(build):
    count = fp.number(fp.get(build, "effects", "tinted_icon_squares"))
    if count is None or count < 1:
        return None
    return "icon_tile", f"{count:g} icon(s) on a tinted square"
