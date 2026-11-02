"""JSON audit reports (``--report``) and summary counters."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .base import ScanResult
from .engine import FileOutcome

__all__ = ["build_clean_report", "build_scan_report", "summarize", "write_report"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _summary(outcomes: Sequence[FileOutcome]) -> dict[str, int]:
    return {
        "files": len(outcomes),
        "cleaned": sum(1 for item in outcomes if item.status == "cleaned"),
        "already_clean": sum(1 for item in outcomes if item.status == "clean"),
        "errors": sum(1 for item in outcomes if not item.ok),
        "items_removed": sum(len(item.removed) for item in outcomes),
    }


def build_clean_report(outcomes: Iterable[FileOutcome]) -> dict[str, object]:
    """Report emitted after a ``clean`` run."""
    items = list(outcomes)
    return {
        "tool": "doc-meta-strip",
        "version": __version__,
        "generated_at": _now(),
        "mode": "clean",
        "summary": _summary(items),
        "files": [item.as_dict() for item in items],
    }


def build_scan_report(results: Iterable[ScanResult]) -> dict[str, object]:
    """Report emitted after a ``scan`` run."""
    items = list(results)
    return {
        "tool": "doc-meta-strip",
        "version": __version__,
        "generated_at": _now(),
        "mode": "scan",
        "summary": {
            "files": len(items),
            "with_metadata": sum(1 for item in items if item.dirty),
            "clean": sum(1 for item in items if not item.dirty and item.ok),
            "errors": sum(1 for item in items if not item.ok),
            "items_found": sum(len(item.findings) for item in items),
        },
        "files": [
            {
                "path": str(item.path),
                "format": item.file_format,
                "status": "error" if not item.ok else ("metadata" if item.dirty else "clean"),
                "findings": [finding.as_dict() for finding in item.findings],
                "warnings": list(item.warnings),
                "error": item.error,
            }
            for item in items
        ],
    }


def summarize(report: dict[str, object]) -> str:
    """One line, human readable summary of a report dictionary."""
    summary = report.get("summary", {})
    if not isinstance(summary, dict):
        return ""
    mode = report.get("mode")
    if mode == "scan":
        return (
            f"{summary.get('files', 0)} file(s) scanned, "
            f"{summary.get('with_metadata', 0)} with metadata, "
            f"{summary.get('items_found', 0)} item(s) found"
            + (f", {summary['errors']} error(s)" if summary.get("errors") else "")
        )
    return (
        f"{summary.get('files', 0)} file(s), {summary.get('cleaned', 0)} cleaned, "
        f"{summary.get('already_clean', 0)} already clean, "
        f"{summary.get('items_removed', 0)} item(s) removed"
        + (f", {summary['errors']} error(s)" if summary.get("errors") else "")
    )


def write_report(report: dict[str, object], destination: str | Path) -> None:
    """Write ``report`` as JSON to a path, or to stdout when ``destination`` is ``-``."""
    payload = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=False)
    if str(destination) == "-":
        sys.stdout.write(payload + "\n")
        return
    path = Path(destination)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload + "\n", encoding="utf-8")
