"""Allow ``python -m doc_meta_strip``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(main())
