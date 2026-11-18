# C1 pin-accuracy harness

Run from the repository root, in order. Evidence goes to a store outside the repo
(default `/home/brodey/projects/piggyback-evidence/c1/<run-id>/`).

1. **Commit first.** `manifest` refuses a dirty worktree.
2. **Capture.** Only the IDs you name are captured; nothing is substituted.
   `python -m bench.c1.capture --run RUN C1-01 C1-02 ... C1-20`
   Each side gets `fingerprint.json`, `screenshot.png`, `evidence.json` (DOM snapshot),
   and `receipt` fields in `fingerprint.json`. A failed side gets `failure.json`.
3. **Lock targets.** Reads only `evidence.json`, writes `targets.json` and `targets.lock.json`.
   `python -m bench.c1.targets --store STORE --run RUN`
4. **Manifest.** `python -m bench.c1.manifest --store STORE --run RUN --seed N`
   Writes a dated manifest and the seeded 5-of-20 spot-check sample.
5. **Score.** Refuses unless the targets lock matches.
   `python -m bench.c1.score --store STORE --run RUN [--replace C1-05=R1]`
   Writes `pins.csv`, `anchors.csv`, `pair_summary.csv` and `verdict.json`.

Tests: `PYTHONPATH=. python -m unittest tests.test_bench_c1` (no browser). The browser test
`tests.test_bench_c1_browser` needs Playwright and runs on the host.

Verdict is `pass`, `fail`, or `incomplete` (any unresolved pair).
