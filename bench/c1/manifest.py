"""Register a C1 run before any capture: a dated manifest locked into the run folder.

The manifest refuses a dirty Piggyback worktree and records the commit, the sha256 of the harness
files and pairs.json, the seed and spot-check sample, and the Chromium version of the bundled
browser, queried directly by launching it. Capture and score refuse to run without it, and capture
refuses when HEAD or the harness files have changed since. It is written once (never replaced).

Usage: python -m bench.c1.manifest --store STORE --run RUN_ID --seed N
"""

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from piggyback.measure import publish_json

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_STORE = Path("/home/brodey/projects/piggyback-evidence/c1")
HARNESS = ("capture.py", "targets.py", "score.py", "manifest.py", "snapshot.py", "pairs.json")
SAMPLE_SIZE = 5
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_folder(store, run_id):
    """The run folder under store. The run ID must be one safe path segment."""
    if not RUN_ID.fullmatch(run_id or ""):
        raise SystemExit(f"refusing: run id {run_id!r} is not one safe path segment")
    return Path(store) / run_id


def spot_check(pair_ids, seed, size=SAMPLE_SIZE):
    """A reproducible sample of `size` pair IDs: the same seed and pool give the same list."""
    if size > len(pair_ids):
        raise SystemExit(f"refusing: spot-check size {size} exceeds the pool of {len(pair_ids)}")
    return sorted(random.Random(seed).sample(sorted(pair_ids), size))


def _git(*args):
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def clean_commit():
    """HEAD of the Piggyback checkout, refused when the worktree has any change."""
    if _git("status", "--porcelain"):
        raise SystemExit("refusing: piggyback worktree is dirty; commit or stash first")
    return _git("rev-parse", "HEAD")


def bundled_chromium():
    """(version, label) of the browser Piggyback launches. Launches it; loads no page."""
    from playwright.sync_api import sync_playwright

    from piggyback.browser import launch

    with sync_playwright() as p:
        browser, label = launch(p)
        try:
            return browser.version, label
        finally:
            browser.close()


def harness_hashes():
    return {name: sha256_file(HERE / name) for name in HARNESS}


def load_manifest(run):
    """The run's locked manifest, or a refusal: no capture or score runs without one."""
    path = run / "manifest.json"
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"refusing: no manifest in {run}; run bench.c1.manifest first")
    return json.loads(path.read_text(encoding="utf-8"))


def check_registered(manifest):
    """Refuse unless HEAD is the registered commit, the tree is clean and harness is unchanged."""
    head = _git("rev-parse", "HEAD")
    if head != manifest["piggyback_commit"]:
        raise SystemExit(f"refusing: HEAD {head[:12]} is not the manifest commit")
    clean_commit()
    if harness_hashes() != manifest["harness_sha256"]:
        raise SystemExit("refusing: harness files changed since the manifest was locked")


def build(store, run_id, seed):
    run = run_folder(store, run_id)
    run.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = run / "manifest.json"
    if path.exists() or path.is_symlink():
        raise SystemExit(f"manifest already locked: {path}")
    commit = clean_commit()
    version, label = bundled_chromium()
    pairs = json.loads((HERE / "pairs.json").read_text(encoding="utf-8"))
    pool = sorted(p["id"] for p in pairs["primary"])
    manifest = {
        "locked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "piggyback_commit": commit,
        "chromium_version": version,
        "browser": label,
        "harness_sha256": harness_hashes(),
        "pairs_sha256": sha256_file(HERE / "pairs.json"),
        "spot_check": {
            "seed": seed,
            "sample_size": SAMPLE_SIZE,
            "pool": pool,
            "sample": spot_check(pool, seed),
        },
    }
    publish_json(path, manifest)
    path.chmod(0o600)
    return path, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument("--run", required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args(argv)
    path, manifest = build(args.store, args.run, args.seed)
    print(json.dumps({"manifest": str(path), **manifest["spot_check"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
