"""Lock a dated C1 run manifest and draw the seeded 5-of-20 spot-check sample.

The manifest refuses a dirty Piggyback worktree, so every capture traces to one commit. It records
the Chromium version from the run's receipts, sha256 of the harness files and pairs.json, and the
spot-check seed and sample. It is written once into the run folder (never into the repository).

Usage: python -m bench.c1.manifest --store STORE --run RUN_ID --seed N
"""

import argparse
import json
import random
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from bench.c1.capture import DEFAULT_STORE, HERE, REPO, load_pairs
from bench.c1.targets import sha256_file

SAMPLE_SIZE = 5
HARNESS = ("capture.py", "targets.py", "score.py", "manifest.py", "snapshot.py", "pairs.json")


def spot_check(pair_ids, seed, size=SAMPLE_SIZE):
    """A reproducible sample of `size` pair IDs: the same seed and pool give the same list."""
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


def chromium_version(run):
    """The single browser version recorded by the run's fingerprint receipts."""
    versions = set()
    for path in run.glob("*/*/fingerprint.json"):
        versions.add(json.loads(path.read_text(encoding="utf-8"))["capture"]["browser_version"])
    if len(versions) != 1:
        raise SystemExit(
            f"refusing: expected one Chromium version in receipts, found {sorted(versions)}"
        )
    return versions.pop()


def build(store, run_id, seed):
    run = Path(store) / run_id
    pairs = load_pairs()
    manifest = {
        "locked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "piggyback_commit": clean_commit(),
        "chromium_version": chromium_version(run),
        "harness_sha256": {name: sha256_file(HERE / name) for name in HARNESS},
        "pairs_sha256": sha256_file(HERE / "pairs.json"),
        "spot_check": {
            "seed": seed,
            "sample_size": SAMPLE_SIZE,
            "pool": sorted(p["id"] for p in pairs["primary"]),
            "sample": spot_check((p["id"] for p in pairs["primary"]), seed),
        },
    }
    path = run / f"manifest-{manifest['locked_at'][:10]}.json"
    if path.exists() or path.is_symlink():
        raise SystemExit(f"manifest already locked: {path}")
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
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


if __name__ == "__main__":
    main()
