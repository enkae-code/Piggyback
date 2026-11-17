#!/bin/sh
# All gates in one run with a real exit code. Run before every push (Prism).
set -e
cd "$(dirname "$0")/.."

# The tool on PATH, else the neuroos-gates venv; a tool in neither place fails the run.
tool() {
    if command -v "$1" >/dev/null 2>&1; then
        command -v "$1"
    elif [ -x "$HOME/.venvs/neuroos-gates/bin/$1" ]; then
        echo "$HOME/.venvs/neuroos-gates/bin/$1"
    else
        echo "check.sh: $1 is on neither PATH nor ~/.venvs/neuroos-gates/bin" >&2
        exit 1
    fi
}
RADON=$(tool radon)
VULTURE=$(tool vulture)

ruff format --check .
ruff check .
[ -z "$("$RADON" cc -nc piggyback)" ] || {
    echo "radon: functions over complexity C:" >&2
    "$RADON" cc -nc piggyback >&2
    exit 1
}
"$VULTURE" piggyback --min-confidence 80
git diff --check
# The browser tests skip at import without Playwright, which would pass silently.
PY="${PIGGYBACK_PYTHON:-$HOME/.venvs/taste-vault/bin/python}"
"$PY" -c "import playwright" || { echo "check.sh: $PY has no Playwright; set PIGGYBACK_PYTHON" >&2; exit 1; }
PYTHONPATH=. "$PY" -m unittest discover -s tests -t .
echo "ALL GATES OK"
