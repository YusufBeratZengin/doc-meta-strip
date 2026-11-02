"""Command line interface for doc-meta-strip."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .base import CleanOptions, DocMetaStripError, ScanResult
from .engine import FileOutcome, UsageError, clean_file, collect_targets, scan_file
from .hook import clean_staged
from .report import build_clean_report, build_scan_report, summarize, write_report

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_METADATA = 3

EPILOG = """\
examples:
  doc-meta-strip scan ./belgeler/
  doc-meta-strip clean rapor.pdf -o rapor_temiz.pdf
  doc-meta-strip clean ./belgeler/ --in-place --backup
  doc-meta-strip clean --staged --report audit.json

exit codes:
  0  success
  1  one or more files could not be processed
  3  --strict was given and sensitive metadata was found
"""


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="*", metavar="PATH", help="file or directory to process")
    parser.add_argument("--json", action="store_true", help="print the JSON report to stdout")
    parser.add_argument("--report", metavar="FILE", help="write a JSON audit report to FILE ('-' for stdout)")
    parser.add_argument("--strict", action="store_true", help="exit 3 when sensitive metadata is found")
    parser.add_argument("--no-recursive", action="store_true", help="do not descend into subdirectories")
    parser.add_argument("--quiet", "-q", action="store_true", help="only print problems")
    parser.add_argument("--strip-dates", action="store_true", help="also remove creation/modification dates")
    parser.add_argument("--password", metavar="TEXT", help="password for encrypted PDF input")
    parser.add_argument("--keep-comments", action="store_true", help="keep Office comments")
    parser.add_argument("--keep-embedded", action="store_true", help="keep embedded files and attachments")
    parser.add_argument("--keep-thumbnails", action="store_true", help="keep embedded preview thumbnails")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doc-meta-strip",
        description="Remove GPS, author, company, editing history and internal paths from "
        "PDF, DOCX, XLSX and image files -- entirely offline.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", "-V", action="version", version=f"doc-meta-strip {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="list files that contain sensitive metadata")
    _add_common_options(scan)

    clean = subparsers.add_parser("clean", help="remove sensitive metadata")
    _add_common_options(clean)
    clean.add_argument("-o", "--output", metavar="FILE", help="write the cleaned file here")
    clean.add_argument("--in-place", action="store_true", help="overwrite the input file")
    clean.add_argument("--backup", action="store_true", help="keep a .bak copy before overwriting")
    clean.add_argument("--staged", action="store_true", help="clean files staged in git and re-add them")
    return parser


def _options(args: argparse.Namespace) -> CleanOptions:
    return CleanOptions(
        strip_dates=args.strip_dates,
        remove_comments=not args.keep_comments,
        remove_embedded=not args.keep_embedded,
        remove_thumbnails=not args.keep_thumbnails,
        password=args.password,
    )


def _print_findings(result: ScanResult) -> None:
    if not result.ok:
        print(f"\u274c {result.path}: {result.error}", file=sys.stderr)
        return
    if not result.findings:
        print(f"\u2714 {result.path}: clean ({result.file_format})")
        return
    print(
        f"\u26a0\ufe0f  {result.path} \u2014 {len(result.findings)} sensitive metadata item(s) "
        f"({result.file_format})"
    )
    for finding in result.findings:
        print(f"      \u2022 {finding.label} [{finding.key}]: {finding.value}")


def _print_outcome(outcome: FileOutcome) -> None:
    if not outcome.ok:
        print(f"\u274c {outcome.path}: {outcome.error}", file=sys.stderr)
        return
    if outcome.status == "clean":
        print(f"\u2714 {outcome.path}: no metadata to remove")
        return
    target = f"{outcome.output}" if outcome.output != outcome.path else "in place"
    print(
        f"\u2705 {outcome.path}: {len(outcome.removed)} item(s) removed "
        f"({outcome.bytes_before} \u2192 {outcome.bytes_after} bytes, {target})"
    )
    for finding in outcome.removed:
        print(f"      \u2022 {finding.label} [{finding.key}]: {finding.value}")
    for warning in outcome.warnings:
        print(f"      \u2139 {warning}")


def run_scan(args: argparse.Namespace) -> int:
    if not args.paths:
        raise UsageError("scan needs at least one file or directory")
    options = _options(args)
    targets = collect_targets([Path(p) for p in args.paths], recursive=not args.no_recursive)
    results: list[ScanResult] = []
    for path in targets:
        try:
            results.append(scan_file(path, options))
        except DocMetaStripError as exc:
            results.append(ScanResult(path=path, file_format="unknown", error=str(exc)))

    if not args.json:
        for result in results:
            if args.quiet and result.ok:
                continue
            _print_findings(result)

    report = build_scan_report(results)
    if args.report:
        write_report(report, args.report)
    if args.json:
        write_report(report, "-")
    elif not args.quiet:
        print()
        print(summarize(report))

    has_errors = any(not result.ok for result in results)
    if has_errors:
        return EXIT_ERROR
    if args.strict and any(result.dirty for result in results):
        return EXIT_METADATA
    return EXIT_OK


def run_clean(args: argparse.Namespace) -> int:
    options = _options(args)

    if args.staged:
        outcomes, restaged = clean_staged(options)
        if not args.json:
            for outcome in outcomes:
                if args.quiet and outcome.ok:
                    continue
                _print_outcome(outcome)
            if not args.quiet:
                if restaged:
                    print(f"\u2705 re-staged {len(restaged)} file(s) in git")
                elif not outcomes:
                    print("\u2714 no supported files are staged")
    else:
        if not args.paths:
            raise UsageError("clean needs a file, a directory, or --staged")
        targets = collect_targets([Path(p) for p in args.paths], recursive=not args.no_recursive)
        if args.output and len(targets) > 1:
            raise UsageError("-o/--output accepts a single input file; use --in-place for batches")
        if not args.output and not args.in_place:
            raise UsageError("choose an output with -o/--output or use --in-place")

        outcomes = []
        for path in targets:
            destination = Path(args.output) if args.output else None
            try:
                outcomes.append(
                    clean_file(
                        path,
                        options,
                        output=destination,
                        in_place=args.in_place,
                        backup=args.backup,
                    )
                )
            except DocMetaStripError as exc:
                outcomes.append(FileOutcome(path=path, file_format="unknown", status="error", error=str(exc)))
        if not args.json:
            for outcome in outcomes:
                if args.quiet and outcome.ok:
                    continue
                _print_outcome(outcome)

    report = build_clean_report(outcomes)
    if args.report:
        write_report(report, args.report)
    if args.json:
        write_report(report, "-")
    elif not args.quiet and outcomes:
        print()
        print(summarize(report))

    return EXIT_OK if all(outcome.ok for outcome in outcomes) else EXIT_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            return run_scan(args)
        return run_clean(args)
    except UsageError as exc:
        print(f"doc-meta-strip: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except DocMetaStripError as exc:
        print(f"doc-meta-strip: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
