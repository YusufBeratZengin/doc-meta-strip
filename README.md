# doc-meta-strip

**Strip GPS coordinates, author names, company names, editing history and internal file paths out of PDF, DOCX, XLSX, JPEG, PNG and WebP files — locally, without uploading anything and without re-encoding a single pixel.**

[![CI](https://github.com/YusufBeratZengin/doc-meta-strip/actions/workflows/ci.yml/badge.svg)](https://github.com/YusufBeratZengin/doc-meta-strip/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## The problem

Every file you share carries a shadow: the camera that took the photo (and where
it was standing), the account name of the person who wrote the document, the
company that owns the laptop, the `C:\Users\...` path the file was saved from,
and the comments and tracked changes that were "deleted" but never accepted.

Publishing a PDF, pushing a screenshot to a public repository, or mailing a
contract to another company routinely leaks all of that. `doc-meta-strip` removes
it in one command, offline, and tells you exactly what it took out.

## Install

```bash
pip install doc-meta-strip
```

Python 3.10 or newer. No system packages, no C compiler, no Office installation,
no Poppler, no ExifTool.

## 30-second example

```bash
$ doc-meta-strip scan sunum.docx
⚠️  sunum.docx — 16 sensitive metadata item(s) (office)
      • Descriptive metadata [core:title]: Fake Internal Title
      • Author / identity [core:creator]: Fake Author
      • Author / identity [core:lastModifiedBy]: Fake Author
      • Editing history [core:revision]: 7
      • Software fingerprint [app:Application]: Fake Office 16.0
      • Organisation [app:Company]: Example Corp
      • Author / identity [app:Manager]: Fake Author
      • Internal file path [app:HyperlinkBase]: C:\Users\fake\Documents\internal
      • Internal file path [custom:ClientPath]: C:\Users\fake\Documents\internal
      • Embedded thumbnail [office:thumbnail]: embedded preview image docProps/thumbnail.jpeg
      • Comment [office:word/comments.xml]: comments part (docx)
      • Editing history [word:tracked-deletion]: deleted secret
      • Editing history [word:rsid]: 1 revision-save id block(s)
      • …

1 file(s) scanned, 1 with metadata, 16 item(s) found

$ doc-meta-strip clean sunum.docx --in-place
✅ sunum.docx: 16 item(s) removed (4309 → 2577 bytes, in place)
      • Author / identity [core:creator]: Fake Author
      • Internal file path [app:HyperlinkBase]: C:\Users\fake\Documents\internal
      • Editing history [word:tracked-deletion]: deleted secret
      • …

1 file(s), 1 cleaned, 0 already clean, 16 item(s) removed

$ doc-meta-strip scan sunum.docx
✔ sunum.docx: clean (office)

1 file(s) scanned, 0 with metadata, 0 item(s) found
```

(Output above is from the repository's own test fixture: the name, company and
path are deliberately fake.)

## What it removes

| Format | Removed | Kept |
| --- | --- | --- |
| **JPEG** | EXIF (camera, serial number, owner, GPS), XMP, IPTC/APP13, APP12, comments, all other APPn segments | JFIF (APP0), ICC colour profile (APP2), Adobe colour transform (APP14), every entropy-coded byte |
| **PNG** | `tEXt`, `zTXt`, `iTXt` (Stable Diffusion / Midjourney prompts live here), `eXIf`, `tIME`, `dSIG` | `IHDR`, `PLTE`, `IDAT`, `iCCP`, `gAMA`, `pHYs`, … |
| **WebP** | `EXIF` and `XMP ` chunks, plus the corresponding feature flags in `VP8X` | `VP8`/`VP8L`/`ALPH`/`ANIM` payloads |
| **DOCX** | `docProps/core.xml`, `app.xml` (company, manager, `HyperlinkBase`), `custom.xml`, thumbnails, `word/comments*.xml`, `word/people.xml`, tracked deletions (`w:del`), comments, `w:rsid*` save IDs, all revision-save attributes | document body, styles, numbering, headers, inserted (`w:ins`) text, images |
| **XLSX** | same `docProps` parts, cell comments (`xl/comments*`), threaded comments, persons, legacy comment drawings | cell values, formulas, styles, charts |
| **PPTX** | `docProps` parts, thumbnails, comment parts | slides, notes, media |
| **PDF** | Info dictionary (`Author`, `Producer`, `Creator`, `Title`, …), XMP packet, `/EmbeddedFiles`, `/JavaScript` name tree, page thumbnails, file-attachment/movie/sound annotations, `/PieceInfo` | text layer, fonts, images, form fields, bookmarks, page structure |

Creation and modification dates are preserved by default — they are usually
needed for document control. Pass `--strip-dates` to remove those too.

## Usage

```
doc-meta-strip scan  <path>...            # report only, never writes
doc-meta-strip clean <path>... -o OUT     # single file -> OUT
doc-meta-strip clean <path>... --in-place # overwrite (add --backup for .bak)
doc-meta-strip clean --staged             # sanitize what is in the git index
```

Options accepted by both subcommands:

| Flag | Meaning |
| --- | --- |
| `--json` | print the JSON audit report to stdout |
| `--report FILE` | write the JSON audit report to `FILE` (`-` for stdout) |
| `--strict` | exit with code 3 when sensitive metadata is found (CI gate) |
| `--no-recursive` | do not descend into subdirectories |
| `--strip-dates` | also remove creation / modification dates |
| `--password TEXT` | password for an encrypted PDF |
| `--keep-comments` | keep Office comments |
| `--keep-embedded` | keep embedded files, attachments and JavaScript |
| `--keep-thumbnails` | keep preview thumbnails |
| `--quiet` / `-q` | print nothing but errors |

Exit codes: `0` success, `1` a file could not be processed, `3` `--strict` found
metadata, `2` bad command line.

## JSON audit report

```bash
doc-meta-strip clean ./belgeler/ --in-place --report audit.json
```

```json
{
  "tool": "doc-meta-strip",
  "version": "0.1.0",
  "mode": "clean",
  "summary": {"files": 1, "cleaned": 1, "already_clean": 0, "errors": 0, "items_removed": 2},
  "files": [
    {
      "path": "belgeler/foto.jpg",
      "format": "image",
      "status": "cleaned",
      "bytes_before": 1318,
      "bytes_after": 1052,
      "removed": [
        {"key": "exif:GPS", "value": "41.008200, 28.978400", "category": "gps", "label": "GPS location"},
        {"key": "exif:Artist", "value": "Fake Author", "category": "identity", "label": "Author / identity"}
      ],
      "warnings": [],
      "error": null
    }
  ]
}
```

The report is the OSINT-defence artefact: it records *which* personal datum was
removed from *which* file, so the sanitisation can be reviewed later.

## Pre-commit integration

`.pre-commit-hooks.yaml` is shipped with the package, so a repository only needs:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/YusufBeratZengin/doc-meta-strip
    rev: v0.1.0
    hooks:
      - id: doc-meta-strip
```

The hook runs `doc-meta-strip clean --staged`, cleans the files in the git index
and re-adds them, so a photo can never be committed with its GPS position. The
same behaviour is available manually: `doc-meta-strip clean --staged`.

## How it stays lossless

* **Images** are never decoded. JPEG marker segments, PNG chunks and WebP RIFF
  chunks are copied byte for byte; only metadata containers are dropped. A pixel
  comparison before/after is part of the test suite.
* **Office files** are ZIP archives of XML parts. The document body is untouched;
  metadata parts are removed and the package relationships and
  `[Content_Types].xml` overrides are pruned so Word/Excel open the result
  without a repair prompt.
* **PDFs** are re-serialised by `pypdf` with content streams copied verbatim.
  A reachability sweep then blanks every object that is no longer referenced, so
  no orphaned copy of the removed XMP packet survives in the file bytes (this is
  tested by scanning the raw output for the leaked strings).

## Why not `mat2` or `exiftool`?

`mat2` and `exiftool` are excellent and far more complete for exotic formats.
`doc-meta-strip` is aimed at a different workflow:

* **Pure Python.** `mat2` pulls in Poppler, Cairo and GdkPixbuf; `exiftool` is a
  large Perl distribution. This package installs with `pip` on Windows, macOS and
  Linux CI images with no system packages, which is what makes it usable as a
  pre-commit hook.
* **`--staged` built in.** Clean the git index and re-add in one step.
* **Audit trail instead of silence.** `--report` writes a JSON diff of what was
  removed, per file; `--strict` turns "found metadata" into a non-zero exit code.
* **Tracked changes and comments.** Unaccepted `w:del` revisions and comments are
  removed from DOCX/XLSX, not just `docProps`.

## Limitations (please read)

* Only structural metadata is touched. Text or pixels are never OCR'd or
  redacted — a name typed into the middle of a paragraph stays there.
* Encrypted PDFs must be unlocked with `--password`; the cleaned copy is written
  **without** encryption. The password is never stored or logged.
* Digital signatures are not preserved. Cleaning a signed PDF or Office document
  invalidates its signature, by design — the bytes change.
* Office documents are validated with `python-docx`/`openpyxl` in the test suite,
  not with Microsoft Word itself. Files are structurally sound OPC packages, but
  exotic documents (macros, embedded OLE, content controls) should be opened once
  before you publish them.
* Very large PDFs are loaded into memory; there is no streaming mode yet.
* This tool does not decide what is sensitive. It removes the metadata categories
  listed above, and `scan` shows you what it found so you can judge.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
bash scripts/check.sh        # ruff + ruff format + mypy + pytest (coverage >= 85%)
```

`scripts/check.sh` is the single quality gate used by CI; it exits 0 only when
lint, formatting, types and the full test suite pass. Tests build every sample
file in a temporary directory — no binary fixtures, no network access.

```
doc_meta_strip/
├── cli.py              # argparse front-end
├── engine.py           # format detection, discovery, atomic writes
├── hook.py             # --staged (git index)
├── report.py           # JSON audit report
├── base.py             # Finding / CleanOptions / Handler
└── handlers/
    ├── image_handler.py   # JPEG / PNG / WebP, byte level
    ├── office_handler.py  # DOCX / XLSX / PPTX, zipfile + xml.etree
    └── pdf_handler.py     # pypdf object graph + garbage collection
```

## License

MIT — see [LICENSE](LICENSE).
