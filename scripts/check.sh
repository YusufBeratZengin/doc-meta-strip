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

REQUIRED_MODULES="import pytest, pytest_cov, ruff, mypy, pypdf, PIL"

PYTHON_BIN="${PYTHON:-}"
if [ -z "${PYTHON_BIN}" ]; then
  for candidate in ".venv/bin/python" ".venv/Scripts/python.exe" "venv/bin/python" "venv/Scripts/python.exe" "python3" "python"; do
    if command -v "${candidate}" >/dev/null 2>&1 && "${candidate}" -c "${REQUIRED_MODULES}" >/dev/null 2>&1; then
      PYTHON_BIN="${candidate}"
      break
    fi
  done
  if [ -z "${PYTHON_BIN}" ]; then
    PYTHON_BIN="python3"
  fi
fi

if ! "${PYTHON_BIN}" -c "${REQUIRED_MODULES}" >/dev/null 2>&1; then
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
