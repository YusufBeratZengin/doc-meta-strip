# Contributing to doc-meta-strip

Thanks for taking the time to help. This project is small on purpose: a pure
Python metadata stripper that has to stay dependency-light and predictable.

## Quick start

```bash
git clone https://github.com/YusufBeratZengin/doc-meta-strip
cd doc-meta-strip
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
bash scripts/check.sh
```

`scripts/check.sh` runs ruff, `ruff format --check`, mypy and the full pytest
suite with a coverage floor of 85%. A pull request is only mergeable when it
exits 0.

## What we are looking for

* New format support (EPUB, TIFF, ODT) as a new handler in
  `doc_meta_strip/handlers/`.
* New metadata that a handler currently misses — please include a generated
  fixture that proves the leak.
* Better error messages, Windows path handling, documentation fixes.

Before writing a big feature, open an issue so we can agree on the shape. The
`good first issue` label marks work that is already scoped.

## Adding a format handler

1. Subclass `doc_meta_strip.base.Handler` and implement `sniff()` and
   `inspect()`. `inspect()` returns `(sanitized_bytes, findings, warnings)`.
2. Register the instance in `doc_meta_strip/handlers/__init__.py`.
3. Never re-encode user content. Copy payload bytes; drop only metadata
   containers.
4. Raise a `DocMetaStripError` subclass (`CorruptFileError`,
   `UnsupportedFormatError`, …) instead of returning partial output.
5. Add the extension to the handler's `extensions` tuple so directory scans pick
   it up.

`scan` runs the exact same code path as `clean` and throws the sanitized bytes
away, so the two commands can never disagree.

## Tests

* Every sample file must be **generated in the test**, not committed as a binary.
* Test data must be obviously fake: `Fake Author`, `Example Corp`,
  `C:\Users\fake\...`. Never add a real photo, document, name or path.
* Every bug fix needs a test that fails before the fix.
* Error paths count: corrupt file, truncated stream, wrong password, read-only
  output directory.

```bash
.venv/bin/python -m pytest -q              # everything
.venv/bin/python -m pytest tests/test_office_handler.py -q
.venv/bin/python -m pytest -q --no-cov      # faster, no coverage gate
```

## Style

* `ruff` and `mypy` are the source of truth; run them before pushing.
* Type annotations everywhere in `doc_meta_strip/`. Public functions get a
  docstring that explains *why*, not what.
* Comments explain non-obvious format details (why APP14 must survive, why the
  PDF object sweep exists). Avoid narrating obvious code.
* Keep the runtime dependency list at two packages unless there is no
  alternative, and justify any addition in `README.md`.

## Commit messages

Short, imperative, English: `Fix PNG iTXt compression flag`, `Add WebP XMP
chunk test`. One logical change per commit.

## Reporting bugs

Please include:

* the command you ran (redact real paths),
* the output of `doc-meta-strip scan <file>`,
* Python version and OS,
* if possible, a **generated** minimal file that reproduces the issue.

Security issues go through [SECURITY.md](SECURITY.md), not the public tracker.

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

Contributions are accepted under the MIT license, the same as the project.
