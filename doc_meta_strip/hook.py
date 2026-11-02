"""``--staged`` support: clean exactly what is in the git index.

The pre-commit workflow is the reason this tool exists in CLI form: run it as a
git hook, and the file you are about to commit is sanitized before it ever
leaves your machine.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from .base import CleanOptions
from .engine import FileOutcome, UsageError, clean_file

__all__ = ["restage", "staged_paths"]


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - git missing on host
        raise UsageError("git is not installed or not on PATH") from exc


def staged_paths(root: Path | None = None) -> list[Path]:
    """Return staged files that exist on disk, resolved against ``root``."""
    base = Path(root) if root is not None else Path.cwd()
    result = _git(base, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise UsageError(f"git diff failed: {message or 'not a git repository'}")
    names = [chunk for chunk in result.stdout.decode("utf-8", "replace").split("\0") if chunk]
    files = [base / name for name in names]
    return [path for path in files if path.is_file()]


def clean_staged(
    options: CleanOptions | None = None,
    root: Path | None = None,
) -> tuple[list[FileOutcome], list[Path]]:
    """Clean staged files in place and re-add the ones that changed.

    Returns the outcomes plus the paths that were re-staged.
    """
    base = Path(root) if root is not None else Path.cwd()
    staged = staged_paths(base)
    targets = [path for path in staged if path.suffix.lower() in _supported()]
    outcomes: list[FileOutcome] = []
    changed: list[Path] = []
    for path in targets:
        outcome = clean_file(path, options, in_place=True, backup=False)
        outcomes.append(outcome)
        if outcome.status == "cleaned":
            changed.append(path)
    if changed:
        restage(base, changed)
    return outcomes, changed


def restage(root: Path, paths: Sequence[Path]) -> None:
    """Re-add ``paths`` to the git index (relative to ``root``)."""
    if not paths:
        return
    relatives = []
    for path in paths:
        try:
            relatives.append(str(path.relative_to(root)))
        except ValueError:  # pragma: no cover - defensive
            relatives.append(str(path))
    result = _git(root, "add", "--", *relatives)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise UsageError(f"git add failed: {message or 'unknown error'}")


def _supported() -> frozenset[str]:
    from .engine import SUPPORTED_SUFFIXES

    return SUPPORTED_SUFFIXES
