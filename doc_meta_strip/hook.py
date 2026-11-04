"""``--staged`` support: clean exactly what is in the git index.

The pre-commit workflow is the reason this tool exists in CLI form: run it as a
git hook, and the file you are about to commit is sanitized before it ever
leaves your machine.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .base import CleanOptions, DocMetaStripError
from .engine import FileOutcome, UsageError, _write_atomic, handler_for

__all__ = ["clean_staged", "restage", "staged_paths", "staged_targets"]


def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            input=input_bytes,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - git missing on host
        raise UsageError("git is not installed or not on PATH") from exc


def _toplevel(base: Path) -> Path:
    """Return the repository root for ``base``.

    ``git diff --name-only`` always reports paths relative to the repository
    root, so running ``clean --staged`` from a subdirectory must resolve them
    against that root.  Without this the paths point at files that do not exist,
    the command silently finds nothing to clean and the leak survives.
    """
    result = _git(base, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        # Not a repository (or git rejected it): keep ``base`` so ``git diff``
        # reports the precise failure to the caller.
        return base
    decoded = os.fsdecode(result.stdout).strip()
    return Path(decoded) if decoded else base


def _rel_posix(toplevel: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(toplevel.resolve()).as_posix()
    except ValueError:  # pragma: no cover - defensive
        return Path(os.path.relpath(path, toplevel)).as_posix()


def _read_staged_entry(toplevel: Path, path: Path) -> tuple[str, str, bytes]:
    """Return ``(mode, rel_posix, blob_bytes)`` for ``path`` from the git index."""
    rel_posix = _rel_posix(toplevel, path)
    ls_result = _git(toplevel, "ls-files", "--stage", "-z", "--", rel_posix)
    if ls_result.returncode != 0:
        message = ls_result.stderr.decode("utf-8", "replace").strip()
        raise DocMetaStripError(f"git ls-files failed for {path}: {message or 'unknown error'}")
    entries = [chunk for chunk in ls_result.stdout.split(b"\0") if chunk]
    if not entries:
        raise DocMetaStripError(f"{path}: file is not staged in the git index")
    if len(entries) != 1:
        raise DocMetaStripError(f"{path}: file has unmerged git index entries")
    header, _, _ = entries[0].partition(b"\t")
    parts = header.decode("ascii", "replace").split()
    if len(parts) != 3 or parts[2] != "0":
        raise DocMetaStripError(f"{path}: file has unmerged git index entries")
    mode, oid, _ = parts
    if mode not in {"100644", "100755"}:
        raise DocMetaStripError(f"{path}: unsupported git index mode {mode}")
    cat_result = _git(toplevel, "cat-file", "blob", oid)
    if cat_result.returncode != 0:
        message = cat_result.stderr.decode("utf-8", "replace").strip()
        raise DocMetaStripError(f"cannot read staged blob for {path}: {message or oid}")
    return mode, rel_posix, cat_result.stdout


def _update_index_blob(toplevel: Path, mode: str, rel_posix: str, payload: bytes) -> None:
    """Write ``payload`` into the git object store and point the index entry at it."""
    hash_result = _git(toplevel, "hash-object", "-w", "--stdin", input_bytes=payload)
    if hash_result.returncode != 0:
        message = hash_result.stderr.decode("utf-8", "replace").strip()
        raise UsageError(f"git hash-object failed: {message or 'unknown error'}")
    new_oid = hash_result.stdout.decode("ascii", "replace").strip()
    update_result = _git(toplevel, "update-index", "--cacheinfo", f"{mode},{new_oid},{rel_posix}")
    if update_result.returncode != 0:
        message = update_result.stderr.decode("utf-8", "replace").strip()
        raise UsageError(f"git update-index failed: {message or 'unknown error'}")


def staged_paths(root: Path | None = None) -> list[Path]:
    """Return staged files in the git index, resolved against the repo root."""
    base = Path(root) if root is not None else Path.cwd()
    toplevel = _toplevel(base)
    result = _git(base, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise UsageError(f"git diff failed: {message or 'not a git repository'}")
    names = [chunk for chunk in result.stdout.split(b"\0") if chunk]
    files = [toplevel / os.fsdecode(name) for name in names]
    return [path for path in files if not path.is_dir()]


def staged_targets(root: Path | None = None) -> list[Path]:
    """Return the staged files that this tool can actually clean."""
    return [path for path in staged_paths(root) if path.suffix.lower() in _supported()]


def clean_staged(
    options: CleanOptions | None = None,
    root: Path | None = None,
    targets: Sequence[Path] | None = None,
) -> tuple[list[FileOutcome], list[Path]]:
    """Clean staged files in the git index while preserving unstaged working-tree edits.

    ``targets`` lets the caller hand over an already discovered list (so the CLI
    can validate it before anything is written); when omitted the staged files
    are looked up here.

    When the working-tree copy is identical to the staged index blob, both the
    working tree and the index are updated so ``git status`` stays clean.  When
    the working tree has unstaged modifications (or the file was removed from
    the working tree after ``git add``), cleaning runs strictly on the index
    blob and updates the index entry in place without touching the working tree.

    Returns the outcomes plus the paths whose index entries were updated.
    """
    opts = options or CleanOptions()
    base = Path(root) if root is not None else Path.cwd()
    toplevel = _toplevel(base)
    if targets is None:
        targets = staged_targets(base)
    outcomes: list[FileOutcome] = []
    changed: list[Path] = []
    worktree_restage: list[Path] = []
    for path in targets:
        try:
            mode, rel_posix, staged_bytes = _read_staged_entry(toplevel, path)
            worktree_bytes: bytes | None = None
            if path.is_file() and not path.is_symlink():
                try:
                    worktree_bytes = path.read_bytes()
                except OSError as exc:
                    raise DocMetaStripError(f"cannot read {path}: {exc}") from exc
            worktree_matches = worktree_bytes is not None and worktree_bytes == staged_bytes
            handler = handler_for(path, staged_bytes)
            cleaned, findings, warnings = handler.clean_bytes(staged_bytes, opts)
            if not findings:
                outcomes.append(
                    FileOutcome(
                        path=path,
                        file_format=handler.name,
                        status="clean",
                        output=None,
                        removed=[],
                        warnings=warnings,
                        bytes_before=len(staged_bytes),
                        bytes_after=len(cleaned),
                    )
                )
                continue
            if worktree_matches:
                try:
                    _write_atomic(path, cleaned)
                except OSError as exc:
                    raise DocMetaStripError(f"cannot write {path}: {exc}") from exc
                worktree_restage.append(path)
            else:
                _update_index_blob(toplevel, mode, rel_posix, cleaned)
                warnings = [*warnings, "working tree has unstaged changes; cleaned staged index blob only"]
            outcomes.append(
                FileOutcome(
                    path=path,
                    file_format=handler.name,
                    status="cleaned",
                    output=path,
                    removed=findings,
                    warnings=warnings,
                    bytes_before=len(staged_bytes),
                    bytes_after=len(cleaned),
                )
            )
            changed.append(path)
        except UsageError:
            raise
        except DocMetaStripError as exc:
            # One broken staged file must not stop the hook from cleaning the rest.
            outcomes.append(FileOutcome(path=path, file_format="unknown", status="error", error=str(exc)))
            continue
    if worktree_restage:
        restage(toplevel, worktree_restage)
    return outcomes, changed


def restage(root: Path, paths: Sequence[Path]) -> None:
    """Re-add ``paths`` to the git index (relative to ``root``)."""
    if not paths:
        return
    relatives = []
    for path in paths:
        try:
            relatives.append(str(path.resolve().relative_to(root.resolve())))
        except ValueError:  # pragma: no cover - defensive
            relatives.append(str(path))
    result = _git(root, "add", "--", *relatives)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise UsageError(f"git add failed: {message or 'unknown error'}")


def _supported() -> frozenset[str]:
    from .engine import SUPPORTED_SUFFIXES

    return SUPPORTED_SUFFIXES
