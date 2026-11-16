"""Read `piggyback measure` fingerprints. Every value is untrusted page data."""

import json
import math
import re
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT = 60
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
MARKDOWN = re.compile(r"([\\`*_\[\]<>|#~!])")
PX = re.compile(r"-?\d+(?:\.\d+)?px")
RGB = re.compile(r"rgba?\([\d.,\s/%]{1,80}\)")
FONT = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .-]{0,39}")
# Heuristic only: a font name that reads like an instruction is dropped. A name made of plain
# words can still get through, so BRIEF.md names fonts and never tells the agent to install them.
COMMAND_WORDS = re.compile(
    r"\b(ignore|instructions?|install|npm|pip|curl|wget|exec|run|prompt|assistant|system)\b",
    re.IGNORECASE,
)


def load(path):
    """Parse one fingerprint file. Raises ValueError for anything that is not a JSON object."""
    if Path(path).stat().st_size > MAX_BYTES:
        raise ValueError(f"{path}: larger than 5 MB")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError:  # JSON and UTF-8 decode errors
        raise ValueError(f"{path}: not valid JSON") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a fingerprint object")
    return data


def clean(text, limit=MAX_TEXT):
    """Text safe inside BRIEF.md: no control characters, markdown escaped, length capped."""
    flat = " ".join(CONTROL.sub(" ", str(text)).split())[:limit]
    return MARKDOWN.sub(r"\\\1", flat)


def get(data, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def number(value):
    """A finite real number, or None. Booleans are not numbers here."""
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value) if is_number and math.isfinite(value) else None


def share(value):
    """A proportion from 0 to 1, or None."""
    found = number(value)
    return found if found is not None and 0 <= found <= 1 else None


def px(value):
    """Pixels from a computed length such as '56px'. None for 'normal', 'auto' or junk."""
    if not isinstance(value, str) or not PX.fullmatch(value):
        return None
    return float(value[:-2])


def length(value):
    """Pixels from a number (spacing gaps are numbers) or a computed length string."""
    found = number(value)
    return found if found is not None else px(value)


def rgb(value):
    """A computed colour string such as 'rgb(0, 0, 0)', or None for anything else."""
    return value if isinstance(value, str) and RGB.fullmatch(value) else None


def rgba(value):
    """(red, green, blue, alpha) from a computed 'rgb()' or 'rgba()' string, or None."""
    if not isinstance(value, str) or not RGB.fullmatch(value) or "%" in value:
        return None
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", value)]
    if len(nums) == 3:
        nums.append(1.0)
    if len(nums) != 4 or max(nums[:3]) > 255 or nums[3] > 1:
        return None
    return tuple(nums)


def fmt(value):
    """A number for BRIEF.md and pins: one decimal at most, so 13.3333 reads 13.3."""
    return f"{round(value, 1):g}"


def font_names(value):
    """Family names from a computed font-family list, instruction-like names dropped."""
    names = []
    for part in str(value).split(","):
        name = " ".join(part.strip().strip("'\"").split())
        if FONT.fullmatch(name) and not COMMAND_WORDS.search(name):
            names.append(name)
    return names


def primary_font(value):
    """The first family of one computed font stack; fallbacks are not read. None if it fails."""
    names = font_names(str(value).split(",", 1)[0])
    return names[0] if names else None


def pairs(rows):
    """[(value, share)] from a histogram list. Values are strings or numbers; bad rows dropped."""
    found = []
    for row in rows if isinstance(rows, list) else []:
        value = get(row, "value")
        weight = share(get(row, "share"))
        if weight is not None and (isinstance(value, str) or number(value) is not None):
            found.append((value, weight))
    return found


def anchor(data, name):
    found = get(data, "anchors", name)
    return found if isinstance(found, dict) else None


def box(value):
    """An anchor box {x, y, width, height} as numbers, or None."""
    coords = {key: number(get(value, key)) for key in ("x", "y", "width", "height")}
    return None if None in coords.values() else coords
