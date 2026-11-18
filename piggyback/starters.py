"""Packaged starter references: read offline, never fetched.

A starter is named `starter:<id>` wherever a reference URL goes. Its measurements ship inside the
package under piggyback/starters/<id>/, made by scripts/regenerate_starters.py. Nothing here
touches the network.
"""

import json
from pathlib import Path

from . import fingerprint as fp

PREFIX = "starter:"
PACKAGED = Path(__file__).parent / "starters"


def is_starter(ref):
    return ref.startswith(PREFIX)


def catalog():
    """Packaged starters as {id, name, license}, sorted by id. Empty when none are packaged."""
    if not PACKAGED.is_dir():
        return []
    folders = sorted(path for path in PACKAGED.iterdir() if (path / "meta.json").is_file())
    rows = []
    for folder in folders:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        rows.append({"id": folder.name, "name": meta["name"], "license": meta["license"]})
    return rows


def check(ref):
    """The id a `starter:<id>` names. ValueError, listing the ids, if it is not packaged."""
    ident = ref[len(PREFIX) :]
    known = [row["id"] for row in catalog()]
    if ident not in known:
        choices = ", ".join(known) or "none are packaged"
        raise ValueError(f"unknown starter {ident!r}; use one of: {choices}")
    return ident


def fingerprint(ref):
    return fp.load(PACKAGED / check(ref) / "fingerprint.json")


def screenshot(ref):
    return PACKAGED / check(ref) / "screenshot.png"


def listing():
    rows = [f"- {PREFIX}{row['id']}: {row['name']} ({row['license']})" for row in catalog()]
    return "\n".join(rows) or "No starters are packaged."
