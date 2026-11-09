# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `--ask-password`, `--password -` (read one line from stdin) and the
  `DOC_META_STRIP_PASSWORD` environment variable keep PDF passwords out of shell
  history and process listings; the value is never written to reports or logs.
- `--lang {en,tr}` and `DOC_META_STRIP_LANG` localize human-readable status and
  summary messages. JSON report keys and values remain English so scripts stay
  stable.
- `scan` and `clean` now strip bytes appended after a JPEG EOI, PNG IEND or WebP
  RIFF payload and report them as `jpeg:trailing_data`, `png:trailing_data` and
  `webp:trailing_data` findings. Hidden payloads appended to an otherwise valid
  image no longer survive cleaning.

### Fixed
- Pictures embedded in Word, Excel and PowerPoint packages (`media/`) are
  cleaned in place (JPEG/PNG/WebP metadata removed); unsupported or unreadable
  images are reported as `office:media-uninspected` instead of being declared
  clean.
- Office XML cleanup is applied even when it produces no finding, so
  revision-save (`rsid`) attributes and author/date attributes are always
  removed; tracked insertions and `rsid` attributes are now reported.
- An empty PDF password is accepted for encrypted PDFs instead of being treated
  as "no password given".
- `--report` no longer fails on file names that are not valid UTF-8; non-ASCII
  characters are escaped in the JSON output.
- JPEG progressive files are now parsed scan by scan instead of copying every
  byte after the first SOS verbatim: APPn/COM metadata segments that appear
  between scans are stripped, while entropy-coded bytes, stuffed `0xFF 0x00`
  bytes and restart markers are preserved bit for bit.
- Office ZIP entries using an unsupported compression method, and unexpected
  decompression failures (`zlib.error`, truncated streams, encrypted entries),
  are reported as controlled `CorruptFileError` failures instead of escaping as
  raw exceptions.
- Newly written `-o` output files now receive the normal umask-derived
  permissions instead of `mkstemp`'s private `0o600`.
- `clean --staged` now reads and sanitizes the staged git index blob directly
  (`git ls-files --stage` + `git cat-file blob`). When the working-tree file
  matches the staged blob, both disk and index are updated; when the working
  tree has unstaged edits (or was deleted after `git add`), only the index entry
  is updated (`git hash-object -w` + `git update-index --cacheinfo`) so unstaged
  changes are never overwritten or accidentally staged into the commit.
- `OfficeHandler` now enforces entry count (`10,000`), per-part uncompressed size
  (`64 MiB`) and total uncompressed archive size (`256 MiB`) limits while
  streaming ZIP entries in 64 KiB chunks, and PNG `zTXt`/`iTXt` preview
  decompression is bounded to `64 KiB`, preventing decompression bombs from
  exhausting memory.
- CI now includes a `package` job on Linux and Windows that builds the wheel,
  installs it into a clean virtual environment without editable/dev dependencies,
  and runs an end-to-end CLI smoke test outside the source tree.
- CLI reconfigures `stdout`/`stderr` on legacy non-UTF-8 Windows codepages
  (such as `cp1252` / `cp1254`) so Unicode status icons and filenames never raise
  `UnicodeEncodeError`.
- `clean --strict` now honours `--strict` and exits with code `3` when sensitive
  metadata was found and removed (`0` when already clean, `1` on error),
  matching `scan --strict`.
- Malformed XML parts inside Office archives (`word/*.xml`, comment parts,
  `.rels`, `[Content_Types].xml`) now raise `CorruptFileError` instead of being
  left untouched with empty findings, preventing tracked deletions or comments
  from surviving silently.
- Damaged PDF page trees (during thumbnail and annotation inspection), `/Info`
  dictionaries, `/Names` / attachment trees and `/Root` trailers now raise
  `CorruptFileError` instead of failing open; unreadable `/Metadata` XMP streams
  emit an explicit warning while still stripping the stream; `/JavaScript` name
  trees and file-attachment/media annotations are now also reported in scan/clean
  findings.
- Word `--keep-comments` now preserves `w:commentReference` / `w:commentRange*`
  anchors in `word/document.xml` instead of detaching kept comments.
- JPEG `JFXX` thumbnail (`APP0`) and `MPF` multi-picture (`APP2`) segments are
  now stripped and reported while preserving `JFIF` and `ICC_PROFILE`.
- Word tracked property/formatting changes (`w:*PrChange`, `w:numberingChange`)
  are dropped whole instead of unwrapped (avoiding invalid nested `w:rPr`) and
  report `word:formatting-change` and `word:change_author`.
- `clean --staged` resolves paths against `git rev-parse --show-toplevel` so
  invocations from subdirectories work, and validates `--report` collisions
  against staged targets before writing.
- `--report` no longer accepts a path that would overwrite an input, an `-o`
  output, or a `--backup` copy; the run stops with a clear error before writing.
- `--report -` and `--json` keep stdout machine-readable (and emit only one JSON
  document when both flags are passed together).
- An unwritable `--report` destination is reported as a normal error instead of
  a traceback.
- `collect_targets` checks skipped directory names relative to the scanned root,
  and newly created `-o` files receive standard umask permissions instead of
  `0o600`.
- `scripts/check.sh` verifies runtime and dev imports (`pypdf`, `Pillow`,
  `pytest`, `pytest_cov`, `ruff`, `mypy`) when selecting a Python interpreter
  and runs on both Linux and Windows in CI.

### Planned
- EPUB metadata support (see issue #3)
- Streaming mode for very large PDFs
- Detection and warning for digitally signed documents

## [0.1.0] - 2026-11-03

First public release.

### Added
- `doc-meta-strip scan` — report sensitive metadata without writing anything.
- `doc-meta-strip clean` — remove metadata from a single file (`-o`), a whole
  directory (`--in-place`), or the git index (`--staged`).
- JPEG sanitizer: drops EXIF (including GPS), XMP, IPTC/APP13, APP12 and comment
  segments while copying every entropy-coded byte untouched.
- PNG sanitizer: drops `tEXt`, `zTXt`, `iTXt`, `eXIf`, `tIME` and `dSIG` chunks
  without touching pixel data.
- WebP sanitizer: drops `EXIF` and `XMP ` chunks and clears the matching `VP8X`
  feature flags.
- DOCX/XLSX/PPTX sanitizer written with `zipfile` and `xml.etree`: clears
  `docProps/core.xml`, `app.xml`, `custom.xml`, preview thumbnails, comments,
  `w:del` tracked deletions, `w:ins` wrappers, `w:rsid*` save identifiers and
  revision-save attributes, then prunes dangling relationships and content-type
  overrides.
- PDF sanitizer on top of `pypdf`: removes the Info dictionary, the XMP packet,
  `/EmbeddedFiles`, `/JavaScript`, page thumbnails, file-attachment annotations
  and `/PieceInfo`; content streams are copied verbatim and unreachable objects
  are blanked so no metadata survives as orphaned bytes.
- `--report FILE` JSON audit report, `--strict` exit code 3 for CI gates,
  `--backup`, `--password` for encrypted PDFs, `--strip-dates`,
  `--keep-comments`, `--keep-embedded`, `--keep-thumbnails`.
- `.pre-commit-hooks.yaml` for use as a git hook.
- 111 tests, 90% line and branch coverage; CI on Linux and Windows with Python
  3.10, 3.12 and 3.13.

### Known limitations
- Encrypted PDFs are written back unencrypted.
- Digital signatures are not preserved (the bytes change, so signatures break).
- No streaming: the whole file is loaded into memory.
- Office output is validated with `python-docx`/`openpyxl`, not Microsoft Word.

[Unreleased]: https://github.com/YusufBeratZengin/doc-meta-strip/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/YusufBeratZengin/doc-meta-strip/releases/tag/v0.1.0
