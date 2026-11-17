"""`piggyback measure-list`: measure a list of pages into one folder per page."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .fingerprint import clean
from .measure import MeasureError, write_measure

MAX_URLS = 500
SLUG_MAX = 80


def read_list(path):
    """[(line number, url)] for URL lines. ValueError names the first bad line."""
    seen, found = {}, []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        if url in seen:
            raise ValueError(f"line {number}: same URL as line {seen[url]}")
        if len(found) == MAX_URLS:
            raise ValueError(f"line {number}: the list has more than {MAX_URLS} URLs")
        seen[url] = number
        found.append((number, url))
    return found


def slug(url):
    """Folder name from host and path. Only [a-z0-9-] survives, so no '/' and no '..'."""
    parts = urlsplit(url)
    text = f"{parts.hostname or ''}{parts.path}".lower()
    name = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:SLUG_MAX].rstrip("-")
    return name or "page"


def plan(list_path, out):
    """[(url, folder)] in list order. ValueError before anything is measured or created."""
    numbers, entries = {}, []
    for number, url in read_list(list_path):
        name = slug(url)
        if name in numbers:
            raise ValueError(f"line {number}: same folder as line {numbers[name]} ({name})")
        numbers[name] = number
        entries.append((url, Path(out) / name))
    return entries


def measure_one(url, folder, viewport, allow_loopback):
    """(status, error). A folder with a fingerprint is kept as it is. A failure is returned."""
    if (folder / "fingerprint.json").exists():
        return "kept", None
    try:
        write_measure(url, folder, viewport=viewport, allow_loopback=allow_loopback)
    except MeasureError as exc:
        return "failed", clean(exc, 200)
    return "measured", None


def run(entries, out, *, viewport, allow_loopback):
    """Measure each entry, print a line per URL, write batch.json. 0 only if nothing failed."""
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    # ponytail: sequential, one browser at a time: about 20 URLs per 30 min at the 90 s cap.
    # Parallel workers if the library needs more.
    for url, folder in entries:
        status, error = measure_one(url, folder, viewport, allow_loopback)
        records.append({"url": url, "dir": str(folder), "status": status, "error": error})
        print(f"{status}: {url}" + (f" ({error})" if error else ""))
    (out_dir / "batch.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    failed = sum(record["status"] == "failed" for record in records)
    print(f"{len(records)} URLs: {failed} failed")
    return 1 if failed else 0
