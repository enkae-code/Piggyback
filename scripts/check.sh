#!/bin/sh
# All gates in one run with a real exit code. Run before every push (Prism).
set -e
cd "$(dirname "$0")/.."

ruff format --check .
ruff check .
[ -z "$(python -m radon cc -nc piggyback 2>/dev/null || ~/.venvs/neuroos-gates/bin/radon cc -nc piggyback)" ] || {
    echo "radon: functions over complexity C:" >&2
    ~/.venvs/neuroos-gates/bin/radon cc -nc piggyback 2>/dev/null || python -m radon cc -nc piggyback >&2
    exit 1
}
~/.venvs/neuroos-gates/bin/vulture piggyback --min-confidence 80
git diff --check
# The browser tests skip at import without Playwright, which would pass silently.
PY="${PIGGYBACK_PYTHON:-$HOME/.venvs/taste-vault/bin/python}"
"$PY" -c "import playwright" || { echo "check.sh: $PY has no Playwright; set PIGGYBACK_PYTHON" >&2; exit 1; }
PYTHONPATH=. "$PY" -m unittest discover -s tests -t .
echo "ALL GATES OK"
