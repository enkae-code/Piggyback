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
NETWORK = re.compile(r"net::ERR_")  # Chromium transport failure, e.g. ERR_SOCKET_NOT_CONNECTED
REDIRECT = re.compile(r"redirected to")
# Failure classes retried once inside the capture window (spec: Capture and substitution)
TRANSIENT = frozenset({"timeout", "network"})
# Failure classes that may justify a replacement (spec: Capture and substitution)
ELIGIBLE = frozenset({"http_status", "cross_host_redirect"})
DECISIONS = ("keep", "replace")
DECISION_FIELDS = ("pair_id", "decision", "reason", "decided_by", "decided_at_utc")


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
    """Failure class of a measure refusal: timeout, network, cross_host_redirect,
    http_status or other."""
    if TIMEOUT.search(error):
        return "timeout"
    if NETWORK.search(error):
        return "network"
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

    detail is the receipt when ok, the failure record otherwise. A timeout or network error is
    retried once; every other failure is recorded and not retried. A challenge page is a
    measurement: it is kept and flagged with the matched text, never discarded.
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
            result = measure_fn(
                url, viewport=VIEWPORT, screenshot=shot, evidence=evidence, failure_screenshot=True
            )
        except MeasureError as exc:
            record = write_failure(
                folder,
                url,
                str(exc),
                status=exc.status,
                final=exc.final_url,
                screenshot=shot,
            )
            if record["class"] in TRANSIENT and n == 1:
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


def _side_entry(url, state, attempt, detail):
    entry = {"state": state, "requested_url": url, "attempt": attempt}
    entry["final_url"] = detail.get("final_url")
    if state == "ok":
        entry["receipt"] = detail
    else:
        entry["class"] = detail["class"]
    return entry


def _side_status(sides):
    if any(s["state"] != "ok" for s in sides.values()):
        return "failed"
    if any(s["receipt"]["suspected_challenge"] for s in sides.values()):
        return "suspected_challenge"
    return "ok"


def capture_pair(pair, run_dir, measure_fn=measure):
    """Both sides of one pair. Returns the pair record written to pair.json.

    Refused when the pair already has attempts: a captured pair is never captured again.
    """
    pair_dir = Path(run_dir) / pair["id"]
    if pair_dir.exists() and any(pair_dir.iterdir()):
        raise SystemExit(f"refusing: {pair['id']} already has attempts in {pair_dir}")
    pair_dir = make_private_dir(pair_dir)
    record = {"pair_id": pair["id"], "sides": {}}
    try:
        for side in SIDES:
            state, attempt, detail = capture_side(pair[side], pair_dir / side, measure_fn)
            record["sides"][side] = _side_entry(pair[side], state, attempt, detail)
        record["status"] = _side_status(record["sides"])
    except BaseException as exc:  # a crash outside MeasureError still leaves a record
        record.update(status="failed", error=str(exc) or repr(exc))
        record["class"] = "other"
        raise
    finally:
        _publish(pair_dir / "pair.json", record)
        os.chmod(pair_dir / "pair.json", 0o600)
    return record


def pair_status(run, pid):
    """The status in a pair's pair.json, or None when the pair has not been captured."""
    path = run / pid / "pair.json"
    return json.loads(path.read_text(encoding="utf-8"))["status"] if path.is_file() else None


def load_decisions(run):
    """{pair_id: entry} from eligibility.json. Refuses an incomplete, unknown or repeated entry."""
    path = run / "eligibility.json"
    if not path.is_file() or path.is_symlink():
        return {}
    decisions = {}
    for entry in json.loads(path.read_text(encoding="utf-8")):
        complete = isinstance(entry, dict) and all(
            isinstance(entry.get(k), str) and entry[k].strip() for k in DECISION_FIELDS
        )
        if not complete or entry["decision"] not in DECISIONS:
            raise SystemExit(f"refusing: eligibility entry needs {DECISION_FIELDS}: {entry}")
        if entry["pair_id"] in decisions:
            raise SystemExit(f"refusing: more than one eligibility decision for {entry['pair_id']}")
        decisions[entry["pair_id"]] = entry
    return decisions


def _failure_classes(run, pid):
    return {
        json.loads(path.read_text(encoding="utf-8"))["class"]
        for path in (run / pid).glob("*/attempt-*/failure.json")
    }


def plan_retained(run, data):
    """[chain] per primary in row order: [primary] when kept, else [primary, reserve, ...].

    A replace decision takes the next unused reserve in row order. A reserve taken as a
    replacement that failed or was flagged needs its own decision, and may be replaced in turn.
    A chain ends at a pair that needs no replacement (ok, or kept). It ends with None when a
    replacement is needed but no reserve is left: the gate is incomplete. Refuses before anything
    is written, until every failed or suspected_challenge primary and every suspected_challenge
    reserve has a decision.
    """
    primaries = [p["id"] for p in data["primary"]]
    reserves = [r["id"] for r in data["reserves"]]
    _check_ids(primaries, reserves)
    decisions = load_decisions(run)
    status = {pid: pair_status(run, pid) for pid in primaries + reserves}
    _check_captured(primaries, status)
    needs = {
        pid
        for pid, s in status.items()
        if s == "suspected_challenge" or (s == "failed" and pid in primaries)
    }
    _check_decision_targets(decisions, needs, status)
    return _assign_chains(run, primaries, reserves, needs, decisions, status)


def _check_ids(primaries, reserves):
    if len(set(primaries)) != len(primaries) or len(set(reserves)) != len(reserves):
        raise SystemExit("refusing: pairs.json repeats a primary or reserve ID")


def _check_captured(primaries, status):
    missing = [pid for pid in primaries if status[pid] is None]
    if missing:
        raise SystemExit(f"refusing: no pair.json for {', '.join(missing)}; capture first")


def _check_decision_targets(decisions, needs, status):
    missing = sorted(needs - set(decisions))
    if missing:
        pid = missing[0]
        raise SystemExit(f"refusing: {pid} is {status[pid]}; record keep or replace for it first")


def _assign_chains(run, primaries, reserves, needs, decisions, status):
    pool, chains, consulted = list(reserves), [], set()
    for pid in primaries:
        chain = [pid]
        while status[chain[-1]] in ("failed", "suspected_challenge"):
            current = chain[-1]
            consulted.add(current)
            entry = decisions.get(current)
            if entry is None:
                raise SystemExit(
                    f"refusing: {current} is {status[current]}; record keep or replace for it first"
                )
            if entry["decision"] == "keep":
                break
            if status[current] == "failed" and not _failure_classes(run, current) & ELIGIBLE:
                raise SystemExit(f"refusing: {current} has no failure record of an eligible class")
            if not pool:
                chain.append(None)
                break
            reserve = pool.pop(0)
            if status[reserve] is None:
                raise SystemExit(f"refusing: {reserve} has no pair.json; capture it first")
            chain.append(reserve)
        chains.append(chain)
    for pid, entry in decisions.items():
        # An unused suspected_challenge reserve still needs a keep decision (fail-closed).
        if pid not in consulted and not (pid in needs and entry["decision"] == "keep"):
            raise SystemExit(
                f"refusing: {pid} is not in the replacement chain; remove its decision"
            )
    return chains


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
