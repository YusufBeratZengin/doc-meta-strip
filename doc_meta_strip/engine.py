"""Format detection, file discovery and the read/clean/write pipeline."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .base import (
    CleanOptions,
    DocMetaStripError,
    Finding,
    Handler,
    ScanResult,
    UnsupportedFormatError,
)
from .handlers import HANDLERS

__all__ = [
    "FileOutcome",
    "SUPPORTED_SUFFIXES",
    "clean_file",
    "collect_targets",
    "handler_for",
    "scan_file",
    "scan_targets",
]

SUPPORTED_SUFFIXES: frozenset[str] = frozenset(
    extension for handler in HANDLERS for extension in handler.extensions
)

SKIP_DIRECTORIES = frozenset({".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__"})


class UsageError(DocMetaStripError):
    """The command was invoked in a way that cannot work."""


@dataclass
class FileOutcome:
    """What happened to one file."""

    path: Path
    file_format: str
    status: str  # "cleaned" | "clean" | "error" | "unsupported"
    output: Path | None = None
    removed: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    bytes_before: int = 0
    bytes_after: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "format": self.file_format,
            "status": self.status,
            "output": str(self.output) if self.output else None,
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "removed": [finding.as_dict() for finding in self.removed],
            "warnings": list(self.warnings),
            "error": self.error,
        }


def handler_for(path: Path, data: bytes) -> Handler:
    """Pick a handler by content sniffing first, then extension."""
    for handler in HANDLERS:
        if handler.sniff(data):
            return handler
    raise UnsupportedFormatError(f"{path.name}: unsupported file format")


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise DocMetaStripError(f"cannot read {path}: {exc}") from exc


def scan_file(path: Path, options: CleanOptions | None = None) -> ScanResult:
    """Inspect a single file without touching it."""
    data = _read(path)
    handler = handler_for(path, data)
    _, findings, warnings = handler.clean_bytes(data, options or CleanOptions())
    return ScanResult(
        path=path,
        file_format=handler.name,
        findings=findings,
        warnings=warnings,
    )


def scan_targets(
    paths: Iterable[Path],
    options: CleanOptions | None = None,
    recursive: bool = True,
) -> list[ScanResult]:
    results: list[ScanResult] = []
    for path in collect_targets(paths, recursive=recursive):
        try:
            results.append(scan_file(path, options))
        except DocMetaStripError as exc:
            results.append(ScanResult(path=path, file_format="unknown", error=str(exc)))
    return results


def collect_targets(paths: Iterable[Path], recursive: bool = True) -> list[Path]:
    """Expand files and directories into a sorted list of candidate files."""
    targets: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            iterator: Iterator[Path] = path.rglob("*") if recursive else path.glob("*")
            for candidate in iterator:
                if not candidate.is_file():
                    continue
                # Only directories *inside* the scanned root are skipped: an
                # ancestor called ``venv`` must not hide the whole tree.
                try:
                    parts = candidate.relative_to(path).parts
                except ValueError:  # pragma: no cover - rglob always prefixes path
                    parts = candidate.parts
                if any(part in SKIP_DIRECTORIES for part in parts[:-1]):
                    continue
                if candidate.suffix.lower() in SUPPORTED_SUFFIXES:
                    targets.append(candidate)
        elif path.is_file():
            targets.append(path)
        else:
            raise UsageError(f"no such file or directory: {path}")
    return sorted(dict.fromkeys(targets))


def _backup_path(path: Path) -> Path:
    candidate = path.with_name(path.name + ".bak")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak.{counter}")
        counter += 1
    return candidate


def _default_file_mode() -> int:
    """Mode a new file would get from ``open()``: ``0o666`` minus the umask."""
    current = os.umask(0)
    os.umask(current)
    return 0o666 & ~current


def _write_atomic(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=".doc-meta-strip-", dir=str(destination.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
        if destination.exists():
            shutil.copymode(destination, temp_name)
        else:
            # mkstemp always creates 0o600; a newly written document should be
            # readable like any other file the user creates.
            os.chmod(temp_name, _default_file_mode())
        os.replace(temp_name, destination)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def clean_file(
    source: Path,
    options: CleanOptions | None = None,
    output: Path | None = None,
    in_place: bool = False,
    backup: bool = False,
) -> FileOutcome:
    """Sanitize ``source`` and write the result.

    ``output`` wins over ``in_place``.  When neither is given the caller is
    expected to treat this as a dry run -- we raise :class:`UsageError` so a
    user can never lose data by accident.
    """
    opts = options or CleanOptions()
    source = Path(source)
    data = _read(source)
    handler = handler_for(source, data)
    cleaned, findings, warnings = handler.clean_bytes(data, opts)

    destination: Path | None
    if output is not None:
        destination = Path(output)
    elif in_place:
        destination = source
    else:
        raise UsageError("choose an output with -o/--output or use --in-place")

    if not findings:
        copied_to: Path | None = None
        if output is not None and Path(output) != source:
            # The copy the user asked for still has to exist, even when the
            # input was already clean.
            copied_to = Path(output)
            try:
                _write_atomic(copied_to, data)
            except OSError as exc:
                raise DocMetaStripError(f"cannot write {copied_to}: {exc}") from exc
        return FileOutcome(
            path=source,
            file_format=handler.name,
            status="clean",
            output=copied_to,
            removed=[],
            warnings=warnings,
            bytes_before=len(data),
            bytes_after=len(cleaned),
        )

    backup_path: Path | None = None
    try:
        if destination != source:
            _write_atomic(destination, cleaned)
        else:
            if backup:
                backup_path = _backup_path(source)
                shutil.copy2(source, backup_path)
            _write_atomic(destination, cleaned)
    except OSError as exc:
        raise DocMetaStripError(f"cannot write {destination}: {exc}") from exc

    if backup_path is not None:
        warnings.append(f"original saved as {backup_path.name}")

    return FileOutcome(
        path=source,
        file_format=handler.name,
        status="cleaned",
        output=destination,
        removed=findings,
        warnings=warnings,
        bytes_before=len(data),
        bytes_after=len(cleaned),
    )
