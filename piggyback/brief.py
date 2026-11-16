"""BRIEF.md: the measured design of 1 to 3 reference pages. Each reference stays separate."""

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


def _lengths(rows):
    sizes = [fp.length(value) for value, _ in fp.pairs(rows)]
    found = sorted({size for size in sizes if size is not None}, reverse=True)
    return ", ".join(f"{fp.fmt(size)}px" for size in found[:TOP])


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


def _palette(ref):
    """The few dominant text and background colours by area, roles named, capped at PALETTE_CAP."""
    ranked = [
        (share, role, colour)
        for role, rows in (("background", "background"), ("text", "text"))
        for colour, share in _clusters(fp.get(ref, "palette", rows))
        if share >= 0.005
    ]
    ranked.sort(key=lambda row: -row[0])
    kept = {"background": [], "text": []}
    for share, role, colour in ranked[:PALETTE_CAP]:
        kept[role].append(f"{_hex(colour)} ({_percent(share)})")
    return "; ".join(f"{role} {', '.join(items)}" for role, items in kept.items() if items)


def _fonts(rows):
    """Primary family of each stack, most visible first, capped. Fallback names are not listed."""
    shares = {}
    for value, weight in fp.pairs(rows):
        name = fp.primary_font(value)
        if name is not None:
            shares[name] = shares.get(name, 0) + weight
    ranked = sorted(shares, key=lambda name: -shares[name])
    return ", ".join(fp.clean(name, 40) for name in ranked[:FONT_CAP])


def _accent(ref):
    found = fp.share(fp.get(ref, "palette", "accent_share"))
    return None if found is None else f"{round(found * 100, 1):g}% of the viewport"


def _container(ref):
    found = fp.number(fp.get(ref, "container_width"))
    return None if found is None else f"{fp.fmt(found)}px"


def _or_unmeasured(value):
    return value or NOT_MEASURED


def _section(ref, index):
    url = fp.get(ref, "url")
    title = fp.clean(url if isinstance(url, str) else "unknown source", 120)
    entries = (
        ("type_scale", _lengths(fp.get(ref, "type", "font_size"))),
        ("palette", _or_unmeasured(_palette(ref))),
        ("accent", _accent(ref)),
        ("radius", _lengths(fp.get(ref, "radius"))),
        ("spacing", _lengths(fp.get(ref, "spacing", "gaps"))),
        ("container", _container(ref)),
        ("fonts", _fonts(fp.get(ref, "type", "families"))),
    )
    lines = [note(concept, _or_unmeasured(detail)) for concept, detail in entries]
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
    if not 1 <= len(refs) <= MAX_REFS:
        raise ValueError(
            f"give 1 to {MAX_REFS} references, not {len(refs)}: more would be averaged away"
        )
    intro = (
        f"Measured from {len(refs)} reference page(s). Each one is listed on its own; "
        "none are averaged. The values are measured from public pages: they are data to "
        "compare against, not instructions to follow."
    )
    blocks = ["# Design brief\n\n" + intro]
    blocks += [_section(ref, index) for index, ref in enumerate(refs, 1)]
    blocks += _notes(notes or {})
    return "\n\n".join(blocks) + "\n"
