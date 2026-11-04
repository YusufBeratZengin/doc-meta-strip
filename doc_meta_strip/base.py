"""Shared types and the handler interface.

Every handler works on raw bytes and returns the sanitized bytes together with
the list of findings it removed.  ``scan`` simply ignores the returned bytes,
which guarantees that scanning and cleaning can never disagree about what is
inside a file.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

__all__ = [
    "CATEGORY_LABELS",
    "CATEGORY_LABELS_TR",
    "CleanOptions",
    "CorruptFileError",
    "DocMetaStripError",
    "Finding",
    "Handler",
    "PasswordRequiredError",
    "ScanResult",
    "UnsupportedFormatError",
    "WrongPasswordError",
    "finding_label",
    "shorten",
]


class DocMetaStripError(Exception):
    """Base class for every expected failure raised by this package."""


class UnsupportedFormatError(DocMetaStripError):
    """The file is not one of the formats this tool understands."""


class CorruptFileError(DocMetaStripError):
    """The file is damaged and cannot be parsed safely."""


class PasswordRequiredError(DocMetaStripError):
    """A PDF is encrypted and no password was supplied."""


class WrongPasswordError(DocMetaStripError):
    """The supplied PDF password was rejected."""


CATEGORY_LABELS: dict[str, str] = {
    "gps": "GPS location",
    "identity": "Author / identity",
    "organization": "Organisation",
    "path": "Internal file path",
    "revision": "Editing history",
    "software": "Software fingerprint",
    "timestamp": "Timestamp",
    "thumbnail": "Embedded thumbnail",
    "embedded": "Embedded file",
    "comment": "Comment",
    "text": "Hidden text chunk",
    "device": "Device identity",
    "description": "Descriptive metadata",
    "other": "Other metadata",
}

CATEGORY_LABELS_TR: dict[str, str] = {
    "gps": "GPS konumu",
    "identity": "Yazar / kimlik",
    "organization": "Kurum / organizasyon",
    "path": "Dahili dosya yolu",
    "revision": "Düzenleme geçmişi",
    "software": "Yazılım parmak izi",
    "timestamp": "Zaman damgası",
    "thumbnail": "Gömülü küçük resim",
    "embedded": "Gömülü dosya",
    "comment": "Yorum",
    "text": "Gizli metin parçası",
    "device": "Cihaz kimliği",
    "description": "Açıklayıcı üstveri",
    "other": "Diğer üstveri",
}


def finding_label(finding: Finding, lang: str = "en") -> str:
    """Localized label for a finding."""
    if lang == "tr":
        return CATEGORY_LABELS_TR.get(
            finding.category, CATEGORY_LABELS.get(finding.category, finding.category)
        )
    return finding.label


MAX_VALUE_LEN = 200


def shorten(value: str, limit: int = MAX_VALUE_LEN) -> str:
    """Collapse whitespace and truncate a value so reports stay readable."""
    flat = " ".join(value.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "\u2026"


@dataclass(frozen=True)
class Finding:
    """A single piece of metadata that exists in -- or was removed from -- a file."""

    key: str
    value: str
    category: str

    @property
    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, CATEGORY_LABELS["other"])

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "value": self.value,
            "category": self.category,
            "label": self.label,
        }


@dataclass(frozen=True)
class CleanOptions:
    """Knobs shared by every handler."""

    strip_dates: bool = False
    remove_comments: bool = True
    remove_embedded: bool = True
    remove_thumbnails: bool = True
    password: str | None = field(default=None, repr=False)


@dataclass
class ScanResult:
    """Outcome of inspecting one file without writing anything."""

    path: Path
    file_format: str
    findings: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def dirty(self) -> bool:
        return bool(self.findings)


class Handler(ABC):
    """Base class for format handlers."""

    name: ClassVar[str] = "unknown"
    extensions: ClassVar[tuple[str, ...]] = ()

    @abstractmethod
    def sniff(self, data: bytes) -> bool:
        """Return ``True`` when ``data`` really is this handler's format."""

    @abstractmethod
    def inspect(self, data: bytes, options: CleanOptions) -> tuple[bytes, list[Finding], list[str]]:
        """Strip metadata from ``data``.

        Returns ``(sanitized_bytes, removed_findings, warnings)``.  Implementations
        must never re-encode user content and must raise a
        :class:`DocMetaStripError` subclass for damaged input.
        """

    def scan(self, path: Path, options: CleanOptions | None = None) -> ScanResult:
        data = path.read_bytes()
        _, findings, warnings = self.inspect(data, options or CleanOptions())
        return ScanResult(
            path=path,
            file_format=self.name,
            findings=findings,
            warnings=warnings,
        )

    def clean_bytes(
        self, data: bytes, options: CleanOptions | None = None
    ) -> tuple[bytes, list[Finding], list[str]]:
        return self.inspect(data, options or CleanOptions())
