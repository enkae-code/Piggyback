"""BRIEF.md: the measured design of 1 to 3 reference pages. Each reference stays separate."""

from . import fingerprint as fp
from .glossary import note

MAX_REFS = 3
MAX_NOTES = 20
TOP = 6
NOT_MEASURED = "not measured"


def _lengths(rows):
    sizes = [fp.length(value) for value, _ in fp.pairs(rows)]
    found = sorted({size for size in sizes if size is not None}, reverse=True)
    return ", ".join(f"{size:g}px" for size in found[:TOP])


def _colours(rows):
    return ", ".join(value for value, _ in fp.pairs(rows) if fp.rgb(value))


def _fonts(rows):
    names = []
    for value, _ in fp.pairs(rows):
        names += [name for name in fp.font_names(value) if name not in names]
    return ", ".join(fp.clean(name, 40) for name in names[:TOP])


def _accent(ref):
    found = fp.share(fp.get(ref, "palette", "accent_share"))
    return None if found is None else f"{round(found * 100, 1):g}% of the viewport"


def _container(ref):
    found = fp.number(fp.get(ref, "container_width"))
    return None if found is None else f"{found:g}px"


def _or_unmeasured(value):
    return value or NOT_MEASURED


def _section(ref, index):
    url = fp.get(ref, "url")
    title = fp.clean(url if isinstance(url, str) else "unknown source", 120)
    entries = (
        ("type_scale", _lengths(fp.get(ref, "type", "font_size"))),
        (
            "palette",
            f"text {_or_unmeasured(_colours(fp.get(ref, 'palette', 'text')))}; "
            f"background {_or_unmeasured(_colours(fp.get(ref, 'palette', 'background')))}",
        ),
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
        "none are averaged."
    )
    blocks = ["# Design brief\n\n" + intro]
    blocks += [_section(ref, index) for index, ref in enumerate(refs, 1)]
    blocks += _notes(notes or {})
    return "\n\n".join(blocks) + "\n"
