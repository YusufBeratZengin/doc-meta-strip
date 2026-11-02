# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |
| < 0.1 | No |

Fixes are released as patch versions on the `0.1.x` line until `0.2.0` ships.

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

Use GitHub's private vulnerability reporting on this repository
("Security" → "Report a vulnerability"). Include:

* affected version (`doc-meta-strip --version`),
* a description of the impact,
* a **generated** minimal file that reproduces the problem (never a real photo,
  document or personal data),
* the exact command you ran.

You can expect an acknowledgement within 7 days and a status update within 30
days. Credit is given in `CHANGELOG.md` unless you ask otherwise.

## Threat model

`doc-meta-strip` is a **local, offline** tool. It has no network code, no
telemetry, no update check and no plugin loading. Reading a malicious file is the
only untrusted input path, and the trust boundary is:

1. **Parsers.** ZIP (`zipfile`), XML (`xml.etree`), JPEG/PNG/WebP headers and PDF
   (`pypdf`) all parse attacker-controlled bytes. `xml.etree` is used without
   entity expansion, so XXE is not reachable from the code paths here.
2. **Decompression.** A crafted Office file can expand enormously
   (zip bomb). Files are loaded into memory; run untrusted input with a memory
   limit (`ulimit -v`) if that matters to you.
3. **Output paths.** `-o` writes exactly where you point it; `--in-place`
   writes to the input path via a temporary file plus `os.replace` in the same
   directory, so a crash cannot leave a half-written document.

## What this tool does not protect you from

* **Content.** It strips structural metadata only. Names, faces, account numbers
  or screenshots inside the document remain.
* **Signatures.** Cleaning invalidates digital signatures. That is intended, but
  it means a cleaned file can no longer prove its origin.
* **Encryption.** Cleaned PDFs are written unencrypted.
* **Backups and history.** `--backup` writes a `.bak` next to the original, and
  `git` keeps the pre-clean blob. Both still contain the metadata; delete them
  deliberately.

## Secrets in this repository

There are none. All sample data in tests is generated at runtime and clearly
fake (`Fake Author`, `Example Corp`, `C:\Users\fake\...`). Any credential
handling in a contribution must go through environment variables and be
documented; hard-coded tokens are rejected.
