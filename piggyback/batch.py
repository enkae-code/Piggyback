"""`piggyback measure-list`: measure a list of pages into one folder per page."""

import re
from pathlib import Path
from urllib.parse import urlsplit

from . import fingerprint as fingerprints
from . import netpolicy
from .measure import MeasureError, publish_json, refuse_links, write_measure

MAX_URLS = 500
SLUG_MAX = 80
LINE_MAX = 2048


def read_list(path):
    """[(line number, url)] for URL lines. ValueError names the first bad line."""
    # utf-8-sig drops a byte order mark at the start of the file; it is not part of the first URL.
    text = Path(path).read_text(encoding="utf-8-sig")
    seen, found = {}, []
    for number, line in enumerate(text.splitlines(), 1):
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
    """Folder name from host, port and path. Only [a-z0-9-] survives, so no '/' and no '..'.
    ValueError for a line that cannot be a URL: that entry fails, the list does not."""
    if len(url) > LINE_MAX:
        raise ValueError(f"longer than {LINE_MAX} characters")
    if any(fingerprints.hidden(char) for char in url):
        raise ValueError("contains control or invisible characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ValueError("not a valid URL") from None
    host = f"{parts.hostname or ''}" + (f":{port}" if port is not None else "")
    text = f"{host}{parts.path}".lower()
    name = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:SLUG_MAX].rstrip("-")
    return name or "page"


def shown(text):
    """Text for the terminal: control and invisible characters print as escapes. clean() is not
    used for URLs: it escapes markdown (`_`, `~`, `!`) and cuts at 60 characters, garbling URLs."""
    return "".join(char if char.isprintable() else repr(char)[1:-1] for char in str(text))


def plan(list_path, out):
    """[(url, folder, problem)] in list order. A problem fails only its own entry. ValueError
    (duplicate URL, two URLs with one folder name, over 500 URLs) refuses the list up front."""
    numbers, entries = {}, []
    for number, url in read_list(list_path):
        try:
            name = slug(url)
        except ValueError as exc:
            entries.append((url, None, str(exc)))
            continue
        if name in numbers:
            raise ValueError(f"line {number}: same folder as line {numbers[name]} ({name})")
        numbers[name] = number
        entries.append((url, Path(out) / name, None))
    return entries


def measure_one(url, folder, viewport, allow_loopback):
    """(status, error). A folder whose fingerprint.json records this URL is kept. Links, unreadable
    or mismatched fingerprints and write errors fail this URL only."""
    try:
        refuse_links(folder, folder / "fingerprint.json", folder / "screenshot.png")
        if (folder / "fingerprint.json").exists():
            recorded = fingerprints.load(folder / "fingerprint.json")
            if recorded.get("url") != netpolicy.local_name(url):
                raise MeasureError(
                    "fingerprint.json records another URL: move the folder aside to re-measure"
                )
            return "kept", None
        write_measure(url, folder, viewport=viewport, allow_loopback=allow_loopback)
    except (MeasureError, OSError, ValueError) as exc:
        return "failed", fingerprints.clean(exc, 200)
    return "measured", None


def run(entries, out, *, viewport, allow_loopback):
    """Measure each entry, print a line per URL, write batch.json. 0 only if nothing failed."""
    out_dir = Path(out)
    refuse_links(out_dir / "batch.json")  # before any URL is measured
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    # ponytail: sequential, one browser at a time: about 20 URLs per 30 min at the 90 s cap.
    # Parallel workers if the library needs more.
    for url, folder, problem in entries:
        if folder is None:
            status, error = "failed", problem
        else:
            status, error = measure_one(url, folder, viewport, allow_loopback)
        dir_name = folder.name if folder else None
        records.append({"url": url, "dir": dir_name, "status": status, "error": error})
        print(f"{status}: {shown(url)}" + (f" ({error})" if error else ""))
    publish_json(out_dir / "batch.json", records)
    failed = sum(record["status"] == "failed" for record in records)
    print(f"{len(records)} URLs: {failed} failed")
    return 1 if failed else 0
