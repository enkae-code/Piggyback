"""Spec v1 reference format: `key: <JSON>` frontmatter plus Borrow/Avoid lines."""

import json
import re

VERSION = 1
BORROW = "- **Borrow:** "
AVOID = "- **Avoid:** "
BY_KINDS = ("human", "agent")
LICENSE_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
REQUIRED = ("piggyback", "by", "by_kind", "license")


def parse_value(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def parse_body(body):
    borrow = avoid = ""
    notes = []
    header = True
    for line in body.split("\n"):
        if header and line.startswith(BORROW):
            borrow = line[len(BORROW) :].strip()
        elif header and line.startswith(AVOID):
            avoid = line[len(AVOID) :].strip()
        elif header and not line.strip():
            continue
        else:
            header = False
            notes.append(line)
    return borrow, avoid, "\n".join(notes).strip()


def parse_ref(text):
    lines = text.split("\n")
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("missing frontmatter fences")
    end = lines.index("---", 1)
    meta = {}
    for line in lines[1:end]:
        key, sep, raw = line.partition(":")
        if sep and key.strip():
            meta[key.strip()] = parse_value(raw.strip())
    return (meta, *parse_body("\n".join(lines[end + 1 :])))


def render_ref(meta, borrow, avoid, notes):
    front = "".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}\n" for key, value in meta.items()
    )
    header = "\n".join(
        line
        for line in (
            f"{BORROW}{borrow}" if borrow else "",
            f"{AVOID}{avoid}" if avoid else "",
        )
        if line
    )
    body = "\n\n".join(part for part in (header, notes.strip()) if part)
    return f"---\n{front}---\n\n{body}\n" if body else f"---\n{front}---\n"


def _is_text(value):
    return isinstance(value, str) and bool(value.strip())


def _license_ok(value):
    if value == "inspiration-only":
        return True
    # ponytail: checks SPDX id shape only, not membership in the SPDX list.
    return isinstance(value, str) and bool(LICENSE_SHAPE.fullmatch(value))


def _field_problems(meta):
    # Each rule: (key, is_valid(value) for present values, message).
    rules = (
        ("piggyback", lambda v: type(v) is int and v == VERSION, f"piggyback must be {VERSION}"),
        ("by", _is_text, "by must be non-empty text"),
        ("by_kind", lambda v: v in BY_KINDS, f"by_kind must be one of: {', '.join(BY_KINDS)}"),
        ("license", _license_ok, "license must be an SPDX id or inspiration-only"),
        ("attribution", _is_text, "attribution must be non-empty text"),
        ("captured_with", _is_text, "captured_with must be non-empty text"),
        (
            "license_url",
            lambda v: isinstance(v, str) and v.startswith(("https://", "http://")),
            "license_url must be an http(s) URL",
        ),
        (
            "sha256",
            lambda v: isinstance(v, str) and bool(SHA256_RE.fullmatch(v)),
            "sha256 must be 64 lowercase hex characters",
        ),
    )
    return [msg for key, ok, msg in rules if key in meta and not ok(meta[key])]


def validate(meta):
    """Return a list of problems; an empty list means the frontmatter is valid v1."""
    missing = [f"{key} is required" for key in REQUIRED if key not in meta]
    return missing + _field_problems(meta)
