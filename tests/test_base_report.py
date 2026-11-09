"""Shared data types, engine helpers and JSON report rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from doc_meta_strip.base import (
    CATEGORY_LABELS,
    CleanOptions,
    Finding,
    ScanResult,
    shorten,
)
from doc_meta_strip.engine import (
    FileOutcome,
    UsageError,
    collect_targets,
    scan_targets,
)
from doc_meta_strip.report import build_clean_report, build_scan_report, summarize, write_report

from conftest import make_jpeg


def test_shorten_collapses_whitespace_and_truncates() -> None:
    assert shorten("  a\n\tb  ") == "a b"
    long_value = "x" * 500
    assert len(shorten(long_value)) == 200
    assert shorten(long_value).endswith("\u2026")


def test_finding_labels_and_dict() -> None:
    finding = Finding(key="exif:GPS", value="1, 2", category="gps")
    assert finding.label == "GPS location"
    payload = finding.as_dict()
    assert payload == {
        "key": "exif:GPS",
        "value": "1, 2",
        "category": "gps",
        "label": "GPS location",
    }


def test_unknown_category_falls_back() -> None:
    assert Finding(key="k", value="v", category="mystery").label == CATEGORY_LABELS["other"]


def test_translated_label_and_summary_api() -> None:
    from doc_meta_strip.base import CATEGORY_LABELS_TR, finding_label

    assert (
        finding_label(Finding(key="gps", value="GPS", category="gps"), lang="tr") == CATEGORY_LABELS_TR["gps"]
    )
    assert "dosya tarandı" in summarize(build_scan_report([]), lang="tr")


def test_report_escapes_non_utf8_surrogate_paths(tmp_path: Path) -> None:
    destination = tmp_path / "report.json"
    write_report({"path": "photo_\udcff.jpg"}, destination)
    assert json.loads(destination.read_text(encoding="utf-8"))["path"] == "photo_\udcff.jpg"


def test_scan_result_properties() -> None:
    clean = ScanResult(path=Path("a"), file_format="pdf")
    assert clean.ok and not clean.dirty

    dirty = ScanResult(path=Path("a"), file_format="pdf", findings=[Finding("k", "v", "other")])
    assert dirty.ok and dirty.dirty

    failed = ScanResult(path=Path("a"), file_format="unknown", error="boom")
    assert not failed.ok


def test_clean_options_defaults() -> None:
    options = CleanOptions()
    assert options.remove_comments and options.remove_embedded and options.remove_thumbnails
    assert options.strip_dates is False
    assert options.password is None
    assert "password" not in repr(options)


def test_clean_report_structure(tmp_path: Path) -> None:
    outcome = FileOutcome(
        path=tmp_path / "a.pdf",
        file_format="pdf",
        status="cleaned",
        output=tmp_path / "a.pdf",
        removed=[Finding("pdf:Info/Author", "Fake Author", "identity")],
        bytes_before=100,
        bytes_after=80,
    )
    report = build_clean_report([outcome])
    assert report["summary"] == {
        "files": 1,
        "cleaned": 1,
        "already_clean": 0,
        "errors": 0,
        "items_removed": 1,
    }
    assert "1 file(s)" in summarize(report)


def test_clean_report_counts_errors(tmp_path: Path) -> None:
    ok = FileOutcome(path=tmp_path / "a.pdf", file_format="pdf", status="clean")
    bad = FileOutcome(path=tmp_path / "b.pdf", file_format="unknown", status="error", error="boom")
    report = build_clean_report([ok, bad])
    assert report["summary"]["errors"] == 1  # type: ignore[index]
    assert "error(s)" in summarize(report)


def test_scan_report_and_summary(tmp_path: Path) -> None:
    dirty = ScanResult(
        path=tmp_path / "a.jpg",
        file_format="image",
        findings=[Finding("exif:GPS", "1, 2", "gps")],
    )
    clean = ScanResult(path=tmp_path / "b.jpg", file_format="image")
    broken = ScanResult(path=tmp_path / "c.jpg", file_format="unknown", error="nope")

    report = build_scan_report([dirty, clean, broken])
    assert report["summary"]["with_metadata"] == 1  # type: ignore[index]
    assert report["files"][2]["status"] == "error"  # type: ignore[index]
    text = summarize(report)
    assert "3 file(s) scanned" in text
    assert "error(s)" in text


def test_write_report_to_file(tmp_path: Path) -> None:
    report = build_scan_report([])
    target = tmp_path / "nested" / "audit.json"
    write_report(report, target)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["tool"] == "doc-meta-strip"
    assert payload["version"] == "0.1.0"


def test_write_report_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    write_report(build_scan_report([]), "-")
    captured = json.loads(capsys.readouterr().out)
    assert captured["mode"] == "scan"


def test_collect_targets_filters_and_sorts(tmp_path: Path) -> None:
    make_jpeg(tmp_path / "b.jpg")
    make_jpeg(tmp_path / "a.jpg")
    (tmp_path / "notes.txt").write_text("skip me", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    make_jpeg(nested / "c.jpg")

    assert [p.name for p in collect_targets([tmp_path])] == ["a.jpg", "b.jpg", "c.jpg"]
    assert [p.name for p in collect_targets([tmp_path], recursive=False)] == ["a.jpg", "b.jpg"]
    assert collect_targets([tmp_path / "a.jpg"]) == [tmp_path / "a.jpg"]


def test_collect_targets_missing_path(tmp_path: Path) -> None:
    with pytest.raises(UsageError):
        collect_targets([tmp_path / "ghost.pdf"])


def test_scan_targets_records_errors(tmp_path: Path) -> None:
    good = make_jpeg(tmp_path / "good.jpg")
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"PK\x03\x04not really a zip")

    results = scan_targets([good, broken])
    assert len(results) == 2
    by_name = {result.path.name: result for result in results}
    assert by_name["broken.docx"].error is not None
    assert by_name["good.jpg"].dirty
