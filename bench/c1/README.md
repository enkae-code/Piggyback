# C1 pin-accuracy harness

Run from the repository root, in order. Evidence goes to a store outside the repo
(default `/home/brodey/projects/piggyback-evidence/c1/<run-id>/`).

1. **Commit, then register.** `manifest` refuses a dirty worktree. It locks the commit, the sha256
   of the harness files and `pairs.json`, the spot-check seed and sample, and the Chromium version of
   the bundled browser (read by launching it). Nothing is captured before it.
   `python -m bench.c1.manifest --store STORE --run RUN --seed N`
2. **Capture.** Only the IDs you name are captured; nothing is substituted. Refused without the
   manifest, when HEAD is not the manifest commit, or when the tree is dirty. Each try gets its own
   `attempt-N/` folder, never overwritten; a pair that already has attempts is never captured again.
   `python -m bench.c1.capture --run RUN C1-01 C1-02 ... C1-20`
   A side is `attempt-N/{fingerprint.json, screenshot.png, evidence.json}`; the receipt is in
   `fingerprint.json`. A failed try gets `failure.json` (class, status, final URL). A bot-wall page
   is kept and flagged `suspected_challenge` with the matched text.
3. **Lock targets.** Reads only `evidence.json` of ok pairs and writes `targets.json` and
   `targets.lock.json`. `python -m bench.c1.targets --store STORE --run RUN`
4. **Replacements, only when needed.** A reserve replaces a primary only when the primary has a
   failure record of an eligible class (`http_status`, `cross_host_redirect`) AND a person has written
   a `replace` decision in `eligibility.json` in the run folder: a list of `{pair_id, decision,
   reason, decided_by, decided_at_utc}`. Reserves are used in order R1, R2, ...
5. **Score.** Refuses unless the manifest and targets lock match and each receipt's Chromium equals
   the manifest. `python -m bench.c1.score --store STORE --run RUN [--replace C1-05=R1]`
   Writes `pins.csv`, `anchors.csv`, `pair_summary.csv` and `verdict.json`.

Tests: `PYTHONPATH=. python -m unittest tests.test_bench_c1 tests.test_bench_c1_round4` (no browser).
The browser test `tests.test_bench_c1_browser` needs Playwright and runs on the host.

Verdict is `pass`, `fail`, or `incomplete` (any unresolved pair).
