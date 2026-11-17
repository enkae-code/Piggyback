"""BRIEF.md: the measured design of 1 to 3 reference pages. Each reference stays separate."""

import json
import math

from . import fingerprint as fp
from .glossary import note

MAX_REFS = 3
MAX_NOTES = 20
TOP = 6
FONT_CAP = 3  # primary families only, most visible first
PALETTE_CAP = 5  # the dominant colours only; a long tail reads as noise
NEAR_RGB = 16  # colours this close in RGB distance read as one colour
NOT_MEASURED = "not measured"
PILL_PX = 999  # radii this large draw a pill whatever the element height
FRAMING = (
    "These are measurements of the public reference pages, not a specification: the agent "
    "decides which values to match."
)
HANDOFF = (
    "These are measurements of the reference pages, not a target. If you use a design skill such "
    "as Impeccable, Hallmark or taste-skill, use it for the method and check its output against "
    "these measurements."
)
MOTION_LIMITS = (
    "- Read without scrolling: motion that starts on scroll is not seen.",
    "- Reduced motion is emulated on the loaded page without a reload: a script that read "
    "matchMedia at load does not reflect it.",
)


def _sizes(rows):
    sizes = [fp.length(value) for value, _ in fp.pairs(rows)]
    # Dedupe after rounding: 13.3333 and 13.34 both print 13.3 (a repeat, or a YAML key clash).
    return sorted({float(fp.fmt(size)) for size in sizes if size is not None}, reverse=True)[:TOP]


def _lengths(rows):
    return ", ".join(f"{fp.fmt(size)}px" for size in _sizes(rows))


def _percent(share):
    return "<1%" if share < 0.01 else f"{round(share * 100)}%"


def _hex(colour):
    red, green, blue, alpha = colour
    text = "#" + "".join(f"{round(channel):02x}" for channel in (red, green, blue))
    return text if alpha >= 1 else f"{text} at {round(alpha * 100)}% opacity"


def _clusters(rows):
    """Near-duplicate colours merged (the first, largest one is kept): [(colour, share)]."""
    found = []
    for value, weight in fp.pairs(rows):
        colour = fp.rgba(value)
        if colour is None:
            continue
        for index, (kept, total) in enumerate(found):
            if math.dist(kept[:3], colour[:3]) <= NEAR_RGB:
                found[index] = (kept, total + weight)
                break
        else:
            found.append((colour, weight))
    return found


def _ranked(ref):
    """(share, role, colour) for the dominant text and background colours, largest first, capped."""
    ranked = [
        (share, role, colour)
        for role, rows in (("background", "background"), ("text", "text"))
        for colour, share in _clusters(fp.get(ref, "palette", rows))
        if share >= 0.005
    ]
    ranked.sort(key=lambda row: -row[0])
    return ranked[:PALETTE_CAP]


def _palette(ref):
    """The few dominant text and background colours by area, roles named, capped at PALETTE_CAP."""
    kept = {"background": [], "text": []}
    for share, role, colour in _ranked(ref):
        kept[role].append(f"{_hex(colour)} ({_percent(share)})")
    return "; ".join(f"{role} {', '.join(items)}" for role, items in kept.items() if items)


def _font_names(rows):
    """Primary family of each stack, most visible first, capped. Fallback names are not listed."""
    shares = {}
    for value, weight in fp.pairs(rows):
        name = fp.primary_font(value)
        if name is not None:
            shares[name] = shares.get(name, 0) + weight
    return sorted(shares, key=lambda name: -shares[name])[:FONT_CAP]


def _fonts(rows):
    return ", ".join(fp.clean(name, 40) for name in _font_names(rows))


def _accent(ref):
    found = fp.share(fp.get(ref, "palette", "accent_share"))
    return None if found is None else f"{round(found * 100, 1):g}% of the first screen"


def _container(ref):
    found = fp.number(fp.get(ref, "container_width"))
    return None if found is None else f"{fp.fmt(found)}px"


def _or_unmeasured(value):
    return value or NOT_MEASURED


def source_title(ref):
    url = fp.get(ref, "url")
    return fp.clean(url if isinstance(url, str) else "unknown source", 120)


def _text(value, limit):
    return fp.clean(value, limit) if isinstance(value, str) and value else NOT_MEASURED


def _receipt(ref):
    """The capture receipt as one fact line, or None when the fingerprint carries none."""
    if not isinstance(fp.get(ref, "capture"), dict):
        return None
    status = fp.number(fp.get(ref, "capture", "status"))
    width, height = (
        fp.number(fp.get(ref, "capture", "viewport", axis)) for axis in ("width", "height")
    )
    screen = (
        NOT_MEASURED if width is None or height is None else f"{fp.fmt(width)}×{fp.fmt(height)}"
    )
    return (
        f"- Capture: HTTP {NOT_MEASURED if status is None else fp.fmt(status)}, "
        f"final URL {_text(fp.get(ref, 'capture', 'final_url'), 120)}, "
        f"{_text(fp.get(ref, 'capture', 'browser'), 40)} "
        f"{_text(fp.get(ref, 'capture', 'browser_version'), 20)} on "
        f"{_text(fp.get(ref, 'capture', 'os'), 40)}, viewport {screen}, "
        f"captured {_text(fp.get(ref, 'capture', 'captured_at'), 40)}"
    )


def _scope(ref):
    """Where the measurements were read: the first screen at the captured size, or the page."""
    width, height = (fp.number(fp.get(ref, "viewport", axis)) for axis in ("width", "height"))
    screen = "first screen"
    if width is not None and height is not None:
        screen += f" ({fp.fmt(width)}×{fp.fmt(height)})"
    return (
        f"- Scope: type, palette, radius, anchors and container width in the {screen}; "
        "container width weighted by visible area; spacing and motion over the whole document."
    )


def _motion_line(row):
    """One motion signature as a brief bullet, or None when it is not fully measured."""
    if not isinstance(row, dict):
        return None
    kind = fp.clean(row.get("kind", ""), 30)
    duration, delay, count = (fp.number(row.get(key)) for key in ("duration", "delay", "count"))
    if not (kind and duration is not None and delay is not None and count is not None):
        return None
    easing = fp.easing(row.get("easing"))
    return (
        f"- {kind}: {fp.fmt(duration)}ms duration, {fp.fmt(delay)}ms delay, "
        f"{easing or NOT_MEASURED}; {fp.motion_props(row.get('props')) or NOT_MEASURED}; "
        f"{fp.fmt(count)} elements"
    )


def _motion(ref):
    """Measured motion signatures and the reduced-motion count, as plain facts, then the limits."""
    lines = []
    rows = fp.get(ref, "motion")
    for row in rows if isinstance(rows, list) else []:
        line = _motion_line(row)
        if line:
            lines.append(line)
    still = fp.number(fp.get(ref, "reduced_motion", "still_running"))
    if still is not None and still >= 0:
        noun = "animation" if still == 1 else "animations"
        lines.append(
            f"- {fp.fmt(still)} {noun} still run with reduced motion requested (whole document)"
        )
    return (lines or [f"- {NOT_MEASURED}"]) + list(MOTION_LIMITS)


def _rows(ref):
    """(title, [(concept, detail)]) for one reference: the measured rows, before any markdown."""
    title = source_title(ref)
    entries = (
        ("type_scale", _lengths(fp.get(ref, "type", "font_size"))),
        ("palette", _or_unmeasured(_palette(ref))),
        ("accent", _accent(ref)),
        ("radius", _lengths(fp.get(ref, "radius"))),
        ("spacing", _lengths(fp.get(ref, "spacing", "gaps"))),
        ("container", _container(ref)),
        ("fonts", _fonts(fp.get(ref, "type", "families"))),
    )
    return title, [(concept, _or_unmeasured(detail)) for concept, detail in entries]


def _check_count(refs):
    if not 1 <= len(refs) <= MAX_REFS:
        raise ValueError(
            f"give 1 to {MAX_REFS} references, not {len(refs)}: more would be averaged away"
        )


def sections(refs):
    """The rows brief() renders, as data for the screen: [(title, [(concept, detail)])]."""
    _check_count(refs)
    return [_rows(ref) for ref in refs]


def _section(ref, index):
    title, entries = _rows(ref)
    header = [line for line in (_receipt(ref), _scope(ref)) if line]
    lines = header + [note(concept, detail) for concept, detail in entries]
    lines.append("## Motion\n\n" + "\n".join(_motion(ref)))
    return f"## Reference {index}: {title}\n\n" + "\n".join(lines)


def _notes(notes):
    blocks = []
    for title, key in (("Borrow", "borrow"), ("Avoid", "avoid")):
        items = [text for text in notes.get(key, []) if isinstance(text, str) and text.strip()]
        if items:
            lines = [f"- {fp.clean(text, 200)}" for text in items[:MAX_NOTES]]
            blocks.append(f"## {title}\n\n" + "\n".join(lines))
    return blocks


def brief(refs, notes=None):
    """BRIEF.md text for 1 to 3 reference fingerprints. Borrow/Avoid lines come from `notes`."""
    _check_count(refs)
    intro = (
        f"Measured from {len(refs)} reference page(s). Each one is listed on its own; "
        f"none are averaged. {FRAMING}"
    )
    blocks = ["# Design brief\n\n" + intro]
    blocks += [_section(ref, index) for index, ref in enumerate(refs, 1)]
    blocks += _notes(notes or {})
    blocks.append(HANDOFF)
    return "\n\n".join(blocks) + "\n"


def _quote(text):
    """One-line YAML double-quoted scalar. JSON escapes are valid YAML: no value opens a block."""
    return json.dumps(text, ensure_ascii=False)


def _colour_tokens(ref):
    """Opaque dominant colours as [(key, hex)], e.g. background-1. Translucent ones are left out."""
    found, counts = [], {}
    for _, role, colour in _ranked(ref):
        if colour[3] >= 1:
            counts[role] = counts.get(role, 0) + 1
            found.append((f"{role}-{counts[role]}", _hex(colour)))
    return found


def _typography_lines(ref):
    sizes = [f"{fp.fmt(size)}px" for size in _sizes(fp.get(ref, "type", "font_size"))]
    fonts = _font_names(fp.get(ref, "type", "families"))
    lines = []
    if sizes:
        lines += ["  scale:"] + [f"    {_quote(px[:-2])}: {_quote(px)}" for px in sizes]
    if fonts:
        lines += ["  body:", f"    fontFamily: {_quote(', '.join(fonts))}"]
    return ["typography:"] + lines if lines else []


def _front(ref, title):
    """YAML frontmatter: only the tokens Impeccable reads (colors, typography, rounded)."""
    lines = [f"name: {_quote(title)}"]
    colours = [f"  {key}: {_quote(hex_)}" for key, hex_ in _colour_tokens(ref)]
    if colours:
        lines += ["colors:"] + colours
    lines += _typography_lines(ref)
    radii = _sizes(fp.get(ref, "radius"))
    # Impeccable treats a radius as a pill only under the key "full"; one pill token is enough.
    rounded = [("full", radii[0])] if radii and radii[0] >= PILL_PX else []
    rounded += [
        (f"radius-{n}", size) for n, size in enumerate((s for s in radii if s < PILL_PX), 1)
    ]
    if rounded:
        lines += ["rounded:"] + [f"  {key}: {_quote(fp.fmt(size) + 'px')}" for key, size in rounded]
    return lines


BODY_SECTIONS = (
    ("Colors", (("palette", "Palette"), ("accent", "Accent"))),
    ("Typography", (("type_scale", "Type scale"), ("fonts", "Fonts"))),
    ("Layout", (("spacing", "Gaps"), ("container", "Container width"))),
    ("Shapes", (("radius", "Radius"),)),
)


def _body(ref, title):
    detail = dict(_rows(ref)[1])
    blocks = [FRAMING, f"Source: {title}", _scope(ref)]
    for heading, items in BODY_SECTIONS:
        lines = [f"- {label}: {detail[concept]}" for concept, label in items]
        lines = [line for line in lines if not line.endswith(f": {NOT_MEASURED}")]
        if lines:
            blocks.append(f"## {heading}\n\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def design_md(ref):
    """DESIGN.md for ONE reference: measured tokens in frontmatter, then a short markdown body."""
    title = source_title(ref)
    return "---\n" + "\n".join(_front(ref, title)) + "\n---\n\n" + _body(ref, title) + "\n"
