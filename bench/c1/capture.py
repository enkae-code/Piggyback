"""Capture the requested C1 pairs at 1440x900 into an evidence store outside the repository.

Only the pair IDs given on the command line are captured. Nothing is substituted automatically:
a failed pair gets a failure-attempt record and a reserve is chosen by a human under the spec.

Usage: python -m bench.c1.capture --run RUN_ID C1-01 C1-02 ... [--store DIR]
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from piggyback.measure import MeasureError, measure, refuse_links
from piggyback.measure import publish_json as _publish

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_STORE = Path("/home/brodey/projects/piggyback-evidence/c1")
VIEWPORT = (1440, 900)
SIDES = (("reference", "reference_url"), ("build", "build_url"))
CHALLENGE = re.compile(
    r"just a moment|access denied|your access has been blocked|verify you are human|captcha",
    re.IGNORECASE,
)
TIMEOUT = re.compile(r"timed out", re.IGNORECASE)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_pairs():
    return json.loads((HERE / "pairs.json").read_text(encoding="utf-8"))


def select_pairs(ids):
    """The primary and reserve pairs named by ids, in the order given. Unknown IDs raise."""
    data = load_pairs()
    known = {p["id"]: p for p in data["primary"] + data["reserves"]}
    missing = [i for i in ids if i not in known]
    if missing:
        raise SystemExit(f"unknown pair id: {', '.join(missing)}")
    return [known[i] for i in ids]


def make_private_dir(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def check_store(store):
    """The store must sit outside the repository: evidence never enters git."""
    resolved = Path(store).resolve()
    if resolved == REPO or REPO in resolved.parents:
        raise SystemExit(f"evidence store {resolved} is inside the repository")
    return make_private_dir(resolved)


def is_challenge(evidence):
    """A bot wall or access denial: the page title or visible text is a challenge, not the site."""
    page = evidence.get("page", {})
    return bool(CHALLENGE.search(f"{page.get('title', '')}\n{page.get('text', '')}"))


def write_failure(side_dir, requested, error, status=None, final=None):
    record = {
        "requested_url": requested,
        "final_url": final,
        "status": status,
        "timestamp_utc": utc_now(),
        "error": error,
        "screenshot": None,  # measure() takes its screenshot only on success
    }
    _publish(side_dir / "failure.json", record)
    os.chmod(side_dir / "failure.json", 0o600)
    return record


def capture_side(url, side_dir, measure_fn=measure):
    """One side into side_dir. Returns ('ok', receipt) or ('failed', failure record).

    A timeout is retried once; every other failure is recorded and not retried.
    """
    side_dir = make_private_dir(side_dir)
    shot, evidence, fp = (
        side_dir / "screenshot.png",
        side_dir / "evidence.json",
        side_dir / "fingerprint.json",
    )
    refuse_links(shot, evidence, fp)
    result = {}
    for attempt in (1, 2):
        try:
            result = measure_fn(url, viewport=VIEWPORT, screenshot=shot, evidence=evidence)
            break
        except MeasureError as exc:
            error = str(exc)
            if attempt == 1 and TIMEOUT.search(error):
                continue
            write_failure(side_dir, url, error)
            return "failed", error
    capture = result.get("capture", {})
    snapshot = json.loads(evidence.read_text(encoding="utf-8")) if evidence.exists() else {}
    if is_challenge(snapshot):
        record = write_failure(
            side_dir,
            url,
            "bot wall: challenge page instead of the site",
            status=capture.get("status"),
            final=capture.get("final_url"),
        )
        return "failed", record["error"]
    _publish(fp, result)
    for path in (shot, evidence, fp):
        if path.exists():
            os.chmod(path, 0o600)
    return "ok", capture


def capture_pair(pair, run_dir, measure_fn=measure):
    """Both sides of one pair. Returns the pair record written to pair.json."""
    pair_dir = make_private_dir(run_dir / pair["id"])
    record = {"pair_id": pair["id"], "sides": {}}
    for side, key in SIDES:
        state, detail = capture_side(pair[key], pair_dir / side, measure_fn)
        record["sides"][side] = {"state": state, "requested_url": pair[key]}
        if state == "ok":
            record["sides"][side]["receipt"] = detail
    record["status"] = (
        "ok" if all(s["state"] == "ok" for s in record["sides"].values()) else "failed"
    )
    _publish(pair_dir / "pair.json", record)
    os.chmod(pair_dir / "pair.json", 0o600)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pairs", nargs="+", help="pair IDs, e.g. C1-01 R1")
    parser.add_argument("--run", required=True, help="run id: the store folder name")
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    args = parser.parse_args(argv)
    store = check_store(args.store)
    run_dir = make_private_dir(store / args.run)
    selected = select_pairs(args.pairs)
    for pair in selected:
        record = capture_pair(pair, run_dir)
        print(f"{pair['id']}: {record['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
