"""Capture the requested C1 pairs at 1440x900 into an evidence store outside the repository.

Only the pair IDs given on the command line are captured, and only in a run that has a registered
manifest (bench.c1.manifest) and a clean worktree at the manifest commit. Every attempt is kept
in its own attempt-N folder; a pair with attempts is never captured again. Nothing is substituted
automatically: a failed pair gets a failure record, and a reserve is chosen under the spec.

Usage: python -m bench.c1.capture --run RUN_ID C1-01 C1-02 ... [--store DIR]
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from bench.c1.manifest import DEFAULT_STORE, HERE, REPO, check_registered, load_manifest, run_folder
from piggyback.measure import MeasureError, measure, refuse_links
from piggyback.measure import publish_json as _publish

VIEWPORT = (1440, 900)
SIDES = ("reference", "build")  # pairs.json keys: each side's URL is pair[side]
CHALLENGE = re.compile(
    r"just a moment|access denied|your access has been blocked|verify you are human|captcha",
    re.IGNORECASE,
)
TIMEOUT = re.compile(r"timed out|Timeout \d+ms exceeded", re.IGNORECASE)
REDIRECT = re.compile(r"redirected to")
# Failure classes that may justify a replacement (spec: Capture and substitution)
ELIGIBLE = frozenset({"http_status", "cross_host_redirect"})


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


def challenge_text(evidence):
    """The matched text when the page title or visible text is a challenge, else None."""
    page = evidence.get("page", {})
    found = CHALLENGE.search(f"{page.get('title', '')}\n{page.get('text', '')}")
    return found.group(0) if found else None


def classify(error, status):
    """Failure class of a measure refusal: timeout, cross_host_redirect, http_status or other."""
    if TIMEOUT.search(error):
        return "timeout"
    if REDIRECT.search(error):
        return "cross_host_redirect"
    if (status is not None and not 200 <= status < 300) or "answered HTTP" in error:
        return "http_status"
    return "other"


def new_attempt(side_dir):
    """A fresh attempt-N folder: numbered after every existing attempt, never reused."""
    side_dir = make_private_dir(side_dir)
    n = 1 + len(list(side_dir.glob("attempt-*")))
    folder = side_dir / f"attempt-{n}"
    folder.mkdir(mode=0o700)
    return folder, n


def write_failure(folder, requested, error, status=None, final=None, screenshot=None):
    shot = Path(screenshot) if screenshot and Path(screenshot).exists() else None
    if shot:
        os.chmod(shot, 0o600)
    record = {
        "requested_url": requested,
        "final_url": final,
        "status": status,
        "timestamp_utc": utc_now(),
        "error": error,
        "class": classify(error, status),
        "screenshot": str(shot) if shot else None,
    }
    _publish(folder / "failure.json", record)
    os.chmod(folder / "failure.json", 0o600)
    return record


def capture_side(url, side_dir, measure_fn=measure):
    """One side, one attempt folder per try. Returns (state, attempt number, detail).

    detail is the receipt when ok, the failure record otherwise. A timeout is retried once; every
    other failure is recorded and not retried. A challenge page is a measurement: it is kept and
    flagged with the matched text, never discarded.
    """
    side_dir = make_private_dir(side_dir)
    while True:
        folder, n = new_attempt(side_dir)
        shot, evidence, fp = (
            folder / "screenshot.png",
            folder / "evidence.json",
            folder / "fingerprint.json",
        )
        refuse_links(shot, evidence, fp)
        try:
            result = measure_fn(url, viewport=VIEWPORT, screenshot=shot, evidence=evidence)
        except MeasureError as exc:
            record = write_failure(
                folder,
                url,
                str(exc),
                status=exc.status,
                final=exc.final_url,
                screenshot=shot,
            )
            if record["class"] == "timeout" and n == 1:
                continue
            return "failed", n, record
        capture = result.get("capture", {})
        snapshot = json.loads(evidence.read_text(encoding="utf-8")) if evidence.exists() else {}
        flag = challenge_text(snapshot)
        if flag:
            result["suspected_challenge"] = flag
        _publish(fp, result)
        for path in (shot, evidence, fp):
            if path.exists():
                os.chmod(path, 0o600)
        return "ok", n, {**capture, "suspected_challenge": flag}


def capture_pair(pair, run_dir, measure_fn=measure):
    """Both sides of one pair. Returns the pair record written to pair.json.

    Refused when the pair already has attempts: a captured pair is never captured again.
    """
    pair_dir = Path(run_dir) / pair["id"]
    if pair_dir.exists() and any(pair_dir.iterdir()):
        raise SystemExit(f"refusing: {pair['id']} already has attempts in {pair_dir}")
    pair_dir = make_private_dir(pair_dir)
    record = {"pair_id": pair["id"], "sides": {}}
    for side in SIDES:
        state, attempt, detail = capture_side(pair[side], pair_dir / side, measure_fn)
        entry = {"state": state, "requested_url": pair[side], "attempt": attempt}
        if state == "ok":
            entry["receipt"] = detail
            entry["final_url"] = detail.get("final_url")
        else:
            entry["final_url"] = detail.get("final_url")
            entry["class"] = detail["class"]
        record["sides"][side] = entry
    if any(s["state"] != "ok" for s in record["sides"].values()):
        record["status"] = "failed"
    elif any(s["receipt"]["suspected_challenge"] for s in record["sides"].values()):
        record["status"] = "suspected_challenge"
    else:
        record["status"] = "ok"
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
    run_dir = run_folder(store, args.run)
    if not run_dir.is_dir():
        raise SystemExit(f"refusing: run {args.run} has no manifest; run bench.c1.manifest first")
    check_registered(load_manifest(run_dir))
    selected = select_pairs(args.pairs)
    for pair in selected:
        record = capture_pair(pair, run_dir)
        print(f"{pair['id']}: {record['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
