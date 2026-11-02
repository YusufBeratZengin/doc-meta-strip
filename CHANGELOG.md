# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- 108 tests, 90% line and branch coverage; CI on Linux and Windows with Python
  3.10, 3.12 and 3.13.

### Known limitations
- Encrypted PDFs are written back unencrypted.
- Digital signatures are not preserved (the bytes change, so signatures break).
- No streaming: the whole file is loaded into memory.
- Office output is validated with `python-docx`/`openpyxl`, not Microsoft Word.

[Unreleased]: https://github.com/YusufBeratZengin/doc-meta-strip/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/YusufBeratZengin/doc-meta-strip/releases/tag/v0.1.0
