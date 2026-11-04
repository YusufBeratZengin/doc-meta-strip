"""Command line interface for doc-meta-strip."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .base import CleanOptions, DocMetaStripError, ScanResult, finding_label
from .engine import FileOutcome, UsageError, clean_file, collect_targets, scan_file
from .hook import clean_staged, staged_targets
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
  3  --strict was given and sensitive metadata was found (or removed)
"""


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="*", metavar="PATH", help="file or directory to process")
    parser.add_argument("--json", action="store_true", help="print the JSON report to stdout")
    parser.add_argument("--report", metavar="FILE", help="write a JSON audit report to FILE ('-' for stdout)")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 3 when sensitive metadata is found (or removed by clean)",
    )
    parser.add_argument("--no-recursive", action="store_true", help="do not descend into subdirectories")
    parser.add_argument("--quiet", "-q", action="store_true", help="only print problems")
    parser.add_argument("--strip-dates", action="store_true", help="also remove creation/modification dates")
    parser.add_argument(
        "--password",
        metavar="TEXT",
        help="password for encrypted PDF input ('-' to read from stdin)",
    )
    parser.add_argument(
        "--ask-password",
        action="store_true",
        help="interactively prompt for the PDF password",
    )
    parser.add_argument(
        "--lang",
        choices=["en", "tr"],
        default=None,
        help="user interface language (en, tr; default: en or DOC_META_STRIP_LANG)",
    )
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


def _resolve_password(args: argparse.Namespace) -> str | None:
    if getattr(args, "ask_password", False):
        try:
            return getpass.getpass("Enter PDF password: ")
        except (EOFError, KeyboardInterrupt):
            raise UsageError("password prompt aborted") from None
    password = getattr(args, "password", None)
    if password == "-":
        try:
            line = sys.stdin.readline()
            return line.rstrip("\r\n")
        except (EOFError, KeyboardInterrupt):
            raise UsageError("password input aborted") from None
    if isinstance(password, str):
        return password
    return os.environ.get("DOC_META_STRIP_PASSWORD")


def _resolve_lang(args: argparse.Namespace) -> str:
    lang = getattr(args, "lang", None)
    if lang == "tr":
        return "tr"
    if lang == "en":
        return "en"
    env_lang = os.environ.get("DOC_META_STRIP_LANG", "").lower()
    if env_lang in ("tr", "turkish", "tr_tr"):
        return "tr"
    return "en"


def _options(args: argparse.Namespace) -> CleanOptions:
    return CleanOptions(
        strip_dates=args.strip_dates,
        remove_comments=not args.keep_comments,
        remove_embedded=not args.keep_embedded,
        remove_thumbnails=not args.keep_thumbnails,
        password=_resolve_password(args),
    )


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - unreadable parent directory
        return path.absolute()


def _reject_report_collisions(
    report: str | None,
    targets: Sequence[Path],
    outputs: Sequence[Path] = (),
    backup_sources: Sequence[Path] = (),
) -> None:
    """Refuse a report path that would overwrite an input, output or backup.

    ``scan belge.pdf --report belge.pdf`` used to replace the document with
    JSON.  The check runs before any file is read or written.
    """
    if not report or report == "-":
        return
    destination = Path(report)
    resolved_report = _resolved(destination)
    for protected in [*targets, *outputs]:
        if resolved_report == _resolved(protected):
            raise UsageError(f"--report would overwrite {protected}; choose a different report path")
    for source in backup_sources:
        backup_name = source.name + ".bak"
        if _resolved(destination.parent) == _resolved(source.parent) and destination.name.startswith(
            backup_name
        ):
            raise UsageError(
                f"--report would overwrite the backup of {source}; choose a different report path"
            )


def _report_goes_to_stdout(args: argparse.Namespace) -> bool:
    """True when stdout must carry JSON only (``--json`` or ``--report -``)."""
    return bool(args.json) or args.report == "-"


def _emit_report(report: dict[str, object], args: argparse.Namespace) -> None:
    """Write the audit report to its file and/or stdout, never twice."""
    if args.report and args.report != "-":
        write_report(report, args.report)
    if _report_goes_to_stdout(args):
        write_report(report, "-")


def _print_findings(result: ScanResult, lang: str = "en") -> None:
    if not result.ok:
        print(f"\u274c {result.path}: {result.error}", file=sys.stderr)
        return
    if not result.findings:
        label = "temiz" if lang == "tr" else "clean"
        print(f"\u2714 {result.path}: {label} ({result.file_format})")
        return
    items_label = "hassas üstveri öğesi" if lang == "tr" else "sensitive metadata item(s)"
    print(f"\u26a0\ufe0f  {result.path} \u2014 {len(result.findings)} {items_label} ({result.file_format})")
    for finding in result.findings:
        flabel = finding_label(finding, lang=lang)
        print(f"      \u2022 {flabel} [{finding.key}]: {finding.value}")


def _print_outcome(outcome: FileOutcome, lang: str = "en") -> None:
    if not outcome.ok:
        print(f"\u274c {outcome.path}: {outcome.error}", file=sys.stderr)
        return
    if outcome.status == "clean":
        label = "kaldırılacak üstveri yok" if lang == "tr" else "no metadata to remove"
        print(f"\u2714 {outcome.path}: {label}")
        return
    if lang == "tr":
        target = f"{outcome.output}" if outcome.output != outcome.path else "aynı dosya üzerine"
        print(
            f"\u2705 {outcome.path}: {len(outcome.removed)} öğe temizlendi "
            f"({outcome.bytes_before} \u2192 {outcome.bytes_after} bayt, {target})"
        )
    else:
        target = f"{outcome.output}" if outcome.output != outcome.path else "in place"
        print(
            f"\u2705 {outcome.path}: {len(outcome.removed)} item(s) removed "
            f"({outcome.bytes_before} \u2192 {outcome.bytes_after} bytes, {target})"
        )
    for finding in outcome.removed:
        flabel = finding_label(finding, lang=lang)
        print(f"      \u2022 {flabel} [{finding.key}]: {finding.value}")
    for warning in outcome.warnings:
        print(f"      \u2139 {warning}")


def run_scan(args: argparse.Namespace) -> int:
    if not args.paths:
        raise UsageError("scan needs at least one file or directory")
    lang = _resolve_lang(args)
    options = _options(args)
    targets = collect_targets([Path(p) for p in args.paths], recursive=not args.no_recursive)
    _reject_report_collisions(args.report, targets)
    results: list[ScanResult] = []
    for path in targets:
        try:
            results.append(scan_file(path, options))
        except DocMetaStripError as exc:
            results.append(ScanResult(path=path, file_format="unknown", error=str(exc)))

    json_stdout = _report_goes_to_stdout(args)
    if not json_stdout:
        for result in results:
            if args.quiet and result.ok:
                continue
            _print_findings(result, lang=lang)

    report = build_scan_report(results)
    _emit_report(report, args)
    if not json_stdout and not args.quiet:
        print()
        print(summarize(report, lang=lang))

    has_errors = any(not result.ok for result in results)
    if has_errors:
        return EXIT_ERROR
    if args.strict and any(result.dirty for result in results):
        return EXIT_METADATA
    return EXIT_OK


def run_clean(args: argparse.Namespace) -> int:
    lang = _resolve_lang(args)
    options = _options(args)

    if args.staged:
        # Discover the index contents first: ``--report`` must never be allowed
        # to overwrite a document that is about to be cleaned.
        targets = staged_targets()
        _reject_report_collisions(args.report, targets)
        outcomes, restaged = clean_staged(options, targets=targets)
        json_stdout = _report_goes_to_stdout(args)
        if not json_stdout:
            for outcome in outcomes:
                if args.quiet and outcome.ok:
                    continue
                _print_outcome(outcome, lang=lang)
            if not args.quiet:
                if restaged:
                    msg = (
                        f"\u2705 git indeksinde {len(restaged)} dosya yeniden sahnelendi"
                        if lang == "tr"
                        else f"\u2705 re-staged {len(restaged)} file(s) in git"
                    )
                    print(msg)
                elif not outcomes:
                    msg = (
                        "\u2714 desteklenen sahnelenmiş dosya yok"
                        if lang == "tr"
                        else "\u2714 no supported files are staged"
                    )
                    print(msg)
    else:
        if not args.paths:
            raise UsageError("clean needs a file, a directory, or --staged")
        targets = collect_targets([Path(p) for p in args.paths], recursive=not args.no_recursive)
        if args.output and len(targets) > 1:
            raise UsageError("-o/--output accepts a single input file; use --in-place for batches")
        if not args.output and not args.in_place:
            raise UsageError("choose an output with -o/--output or use --in-place")
        destinations = [Path(args.output)] if args.output else []
        _reject_report_collisions(
            args.report,
            targets,
            destinations,
            targets if args.backup else [],
        )
        json_stdout = _report_goes_to_stdout(args)

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
        if not json_stdout:
            for outcome in outcomes:
                if args.quiet and outcome.ok:
                    continue
                _print_outcome(outcome, lang=lang)

    report = build_clean_report(outcomes)
    _emit_report(report, args)
    if not json_stdout and not args.quiet and outcomes:
        print()
        print(summarize(report, lang=lang))

    has_errors = any(not outcome.ok for outcome in outcomes)
    if has_errors:
        return EXIT_ERROR
    if args.strict and any(outcome.removed for outcome in outcomes):
        return EXIT_METADATA
    return EXIT_OK


def _configure_stdio() -> None:
    """Ensure Unicode symbols and file names never crash on legacy Windows codepages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            encoding = getattr(stream, "encoding", None) or "utf-8"
            if encoding.lower().replace("-", "") != "utf8":
                reconfigure(encoding="utf-8", errors="replace")
            else:
                reconfigure(errors="replace")
        except Exception:  # pragma: no cover - non-standard stream wrapper
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdio()
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
