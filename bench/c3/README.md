# C3 acceptance checklist and prompt assembly

Frozen by `phase-c-sets.md`, section C3. The task rows, the shared instruction and the five arm
blocks are copied verbatim; `tests/fixtures/c3/spec_text.json` holds the frozen text and
`tests/test_bench_c3.py` asserts equality with it.

## Run order

1. Expand the prompts (no model runs here):
   `PYTHONPATH=. python -m bench.c3.prompts OUT/prompts`
   Each file has the `[[RUNTIME_AND_TOOL_INSTRUCTIONS: filled at manifest time]]` placeholder,
   which is replaced when the manifest is locked.
2. After each build and each correction turn, run the checklist on the page:
   `PYTHONPATH=. python -m bench.c3.checklist PAGE.html --task T01 > OUT/T01-arm-turn0.json`
   `PAGE` is a file path or a loopback `http(s)` URL (`127.0.0.1` or `localhost`). Any other host
   is refused before a browser starts. Exit 0 means every item passed; exit 1 means the JSON
   holds the `correction_message`.
3. Use the `correction_message` as the single follow-up turn. Stop once the checklist passes, or
   after five turns.

Playwright is needed for step 2 (`pip install playwright` and `playwright install chromium`, or
the repo's measure extra). The browser tests, `tests/test_bench_c3_browser.py`, run the checklist
on `tests/fixtures/c3/` pages and skip when Playwright is missing.

## Checklist order and rules

1. **render**: passes when the body has non-empty rendered text (`innerText`, trimmed) and no
   uncaught `pageerror` happened. Console errors (for example a 404 image) are recorded in the
   JSON and never fail this check.
2. **content**: one predicate per content item of the task row (see `tasks.json`).
   Kinds: `text` (regex search on the body text, case-insensitive), `count` (matching elements
   `>=` min), `control_name` (an accessible name of a button, link, textbox, or similar control
   matches a regex).
3. **overflow**: `scrollWidth > clientWidth` at 1440 px, then at 390 px, fails.
4. **axe**: axe-core 4.14.1, tags `wcag2a` and `wcag2aa`, at 1440 px. Violations fail. Incomplete
   results are recorded and never fail. An axe error fails closed.

The failure message is fixed text: `The page failed these checklist items:` followed by one
`- ` line per failed item, then `Fix each listed item and return the complete page.`

## Vendored axe-core

- Package: `axe-core` 4.14.1, from the official npm tarball
  (`npm pack axe-core@4.14.1`, npm integrity `sha512-FHfa+lmNjQZfJz1q8wKqIDwC6DExcHt/pkjNQb09a3NPjCKg2rhu5qb2S4r76ASyFNNtuFooJpZe37OgudVGtQ==`).
- `vendor/axe-core/axe.min.js` sha256: `29348999450cdf2849a8e70b7912fc2da85a849da39c0d0d5394f51d4a9ed95c`
- `vendor/axe-core/LICENSE` (MPL-2.0) sha256: `af175b9d96ee93c21a036152e1b905b0b95304d4ae8c2c921c7609100ba8df7e`
- `vendor/axe-core/LICENSE-3RD-PARTY.txt` sha256: `4f8563870d0fca38bbc3e00b6f670cb7fa9f380ba9f26a7f7d1184a6b18b1653`

## Limits

Axe findings are automated checks. They do not certify accessibility. The content predicates
check that a required item exists, not that it is good.
