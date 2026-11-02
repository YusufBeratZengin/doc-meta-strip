#!/usr/bin/env bash
#
# Single quality gate for doc-meta-strip.
#
#   bash scripts/check.sh
#
# Runs, in order: ruff lint, ruff format check, mypy type check, pytest with a
# coverage floor of 85%.  Exits 0 only if every step passes.  No network access
# is required: every test builds its own sample files.

set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON:-}"
if [ -z "${PYTHON_BIN}" ]; then
  if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
  elif [ -x ".venv/Scripts/python.exe" ]; then
    PYTHON_BIN=".venv/Scripts/python.exe"
  elif [ -x "venv/bin/python" ]; then
    PYTHON_BIN="venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

if ! "${PYTHON_BIN}" -c "import pytest, pytest_cov, ruff, mypy" >/dev/null 2>&1; then
  cat >&2 <<EOF
error: development dependencies are missing for ${PYTHON_BIN}

  ${PYTHON_BIN} -m pip install -e ".[dev]"
EOF
  exit 1
fi

echo "==> python: $("${PYTHON_BIN}" --version 2>&1)"

echo "==> ruff check"
"${PYTHON_BIN}" -m ruff check .

echo "==> ruff format --check"
"${PYTHON_BIN}" -m ruff format --check .

echo "==> mypy"
"${PYTHON_BIN}" -m mypy doc_meta_strip

echo "==> pytest (coverage floor 85%)"
"${PYTHON_BIN}" -m pytest

echo
echo "All checks passed."
