"""OpenXML (DOCX / XLSX / PPTX) metadata sanitizer built on ``zipfile`` + ``xml.etree``.

No Word, Excel or third party Office library is involved: the document is a ZIP
archive of XML parts and this module edits the parts directly.  Pixel data,
cell values, styles and the document body are left alone; only metadata parts,
tracked-change markup, comments and thumbnails disappear.
"""

from __future__ import annotations

import contextlib
import io
import posixpath
import re
import zipfile
import zlib
from typing import Any
from xml.etree import ElementTree as ET

from ..base import (
    CleanOptions,
    CorruptFileError,
    Finding,
    Handler,
    UnsupportedFormatError,
    shorten,
)

__all__ = [
    "MAX_ARCHIVE_ENTRIES",
    "MAX_PART_BYTES",
    "MAX_TOTAL_UNCOMPRESSED_BYTES",
    "OfficeHandler",
]

MAX_ARCHIVE_ENTRIES = 10_000
MAX_PART_BYTES = 64 * 1024 * 1024  # 64 MiB per uncompressed member
MAX_TOTAL_UNCOMPRESSED_BYTES = 256 * 1024 * 1024  # 256 MiB across the archive
_ZIP_READ_CHUNK_BYTES = 64 * 1024

NS_CORE = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_DCTERMS = "http://purl.org/dc/terms/"
NS_EXT = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
NS_CUSTOM = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

CORE_CATEGORIES = {
    "creator": "identity",
    "lastModifiedBy": "identity",
    "title": "description",
    "subject": "description",
    "description": "description",
    "keywords": "description",
    "category": "description",
    "contentStatus": "description",
    "revision": "revision",
    "lastPrinted": "timestamp",
    "created": "timestamp",
    "modified": "timestamp",
    "language": "other",
    "identifier": "other",
    "version": "other",
}

APP_CATEGORIES = {
    "Company": "organization",
    "Manager": "identity",
    "HyperlinkBase": "path",
    "Template": "path",
    "Application": "software",
    "AppVersion": "software",
    "TotalTime": "revision",
    "DocSecurity": "other",
    "HeadingPairs": "description",
    "TitlesOfParts": "description",
}

#: ``app.xml`` children that survive: harmless counters.
APP_ALLOWED = frozenset(
    {
        "Pages",
        "Words",
        "Characters",
        "CharactersWithSpaces",
        "Lines",
        "Paragraphs",
        "Slides",
        "Notes",
        "HiddenSlides",
        "MMClips",
        "ScaleCrop",
        "LinksUpToDate",
        "SharedDoc",
        "HyperlinksChanged",
        "PresentationFormat",
    }
)

W_DROP = frozenset(
    {
        "del",
        "moveFrom",
        "commentReference",
        "commentRangeStart",
        "commentRangeEnd",
        "rsids",
        "cellDel",
        "delText",
        # ``*PrChange`` carries a snapshot of the *previous* properties.  Merging
        # that snapshot into the live element (an unwrap) nests e.g. ``w:rPr``
        # inside ``w:rPr``, which is invalid OpenXML and makes Word offer to
        # repair the document, so these are dropped whole.
        "rPrChange",
        "pPrChange",
        "sectPrChange",
        "tblPrChange",
        "trPrChange",
        "tcPrChange",
        "tblGridChange",
        "numberingChange",
    }
)

W_UNWRAP = frozenset(
    {
        "ins",
        "moveTo",
        "cellIns",
        "cellMerge",
    }
)

RSID_PREFIXES = ("rsid", "rsidR", "rsidP", "rsidTr", "rsidSect", "rsidDel", "rsidRPr", "rsidRDefault")

REL_TYPES_COMMENTS = (
    "/comments",
    "/threadedComment",
)

TEXT_PART_PREFIXES = ("word/document", "word/header", "word/footer", "word/footnotes", "word/endnotes")
TEXT_PART_NAMES = frozenset({"word/document.xml", "word/footnotes.xml", "word/endnotes.xml"})


def qname(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


CUSTOM_PROPERTY_HINTS = (
    (("path", "dir", "folder", "file", "link", "location"), "path"),
    (("author", "reviewer", "manager", "owner", "user", "editor", "name"), "identity"),
    (("company", "organi", "org", "client", "customer", "department"), "organization"),
    (("date", "time", "created", "modified", "printed"), "timestamp"),
)


def classify_custom_property(name: str) -> str:
    """Guess a report category from a user defined property name."""
    lowered = name.lower()
    for hints, category in CUSTOM_PROPERTY_HINTS:
        if any(hint in lowered for hint in hints):
            return category
    return "other"


def localname(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[1]
    return tag


def namespace_of(tag: Any) -> str:
    if isinstance(tag, str) and tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


def serialize(root: ET.Element) -> bytes:
    body = ET.tostring(root, encoding="unicode")
    return (XML_DECLARATION + body).encode("utf-8")


DEFAULT_NS_PATTERN = re.compile(rb'xmlns="([^"]+)"')


def _parse(payload: bytes) -> ET.Element:
    """Parse a part while preserving its namespace prefixes.

    ``xml.etree`` is global about prefix registration, so the part's own
    prefixes (including the default namespace) are registered immediately
    before parsing and the result is serialised right after.
    """
    try:
        for _event, item in ET.iterparse(io.BytesIO(payload), events=("start-ns",)):
            prefix, uri = item
            if prefix.startswith("ns"):
                continue
            try:
                ET.register_namespace(prefix, uri)
            except ValueError:  # pragma: no cover - invalid prefix in source file
                continue
    except ET.ParseError:
        pass
    match = DEFAULT_NS_PATTERN.search(payload[:4096])
    if match:
        with contextlib.suppress(ValueError):  # pragma: no cover - invalid prefix
            ET.register_namespace("", match.group(1).decode("utf-8"))
    return ET.fromstring(payload)


class OfficeHandler(Handler):
    name = "office"
    extensions = (".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm")
    max_entries: int = MAX_ARCHIVE_ENTRIES
    max_part_bytes: int = MAX_PART_BYTES
    max_total_bytes: int = MAX_TOTAL_UNCOMPRESSED_BYTES

    def __init__(
        self,
        *,
        max_entries: int | None = None,
        max_part_bytes: int | None = None,
        max_total_bytes: int | None = None,
    ) -> None:
        if max_entries is not None:
            self.max_entries = max_entries
        if max_part_bytes is not None:
            self.max_part_bytes = max_part_bytes
        if max_total_bytes is not None:
            self.max_total_bytes = max_total_bytes

    def sniff(self, data: bytes) -> bool:
        return data.startswith(b"PK\x03\x04")

    def _read_archive(self, data: bytes) -> tuple[dict[str, bytes], dict[str, zipfile.ZipInfo]]:
        """Validate and unpack an OpenXML ZIP archive within memory safety bounds."""
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise CorruptFileError(f"not a valid ZIP/OpenXML file: {exc}") from exc

        infos: dict[str, zipfile.ZipInfo] = {}
        parts: dict[str, bytes] = {}
        total_bytes = 0

        with archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            if len(entries) > self.max_entries:
                raise CorruptFileError(
                    f"ZIP archive has too many entries ({len(entries)} > {self.max_entries})"
                )
            allowed_compressions = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            for info in entries:
                if info.compress_type not in allowed_compressions:
                    raise CorruptFileError(
                        f"unsupported compression method in OpenXML ZIP entry {info.filename}: "
                        f"{info.compress_type}"
                    )
                if info.file_size > self.max_part_bytes:
                    raise CorruptFileError(
                        f"ZIP entry {info.filename} exceeds maximum uncompressed size "
                        f"({info.file_size} > {self.max_part_bytes} bytes)"
                    )
                if total_bytes + info.file_size > self.max_total_bytes:
                    raise CorruptFileError(
                        f"ZIP archive exceeds maximum total uncompressed size ({self.max_total_bytes} bytes)"
                    )
                chunks: list[bytes] = []
                part_bytes = 0
                try:
                    with archive.open(info, "r") as stream:
                        while chunk := stream.read(_ZIP_READ_CHUNK_BYTES):
                            part_bytes += len(chunk)
                            total_bytes += len(chunk)
                            if part_bytes > self.max_part_bytes or total_bytes > self.max_total_bytes:
                                raise CorruptFileError(
                                    f"ZIP entry {info.filename} exceeds maximum uncompressed size"
                                )
                            chunks.append(chunk)
                except CorruptFileError:
                    raise
                except (
                    zipfile.BadZipFile,
                    zipfile.LargeZipFile,
                    zlib.error,
                    EOFError,
                    RuntimeError,
                    ValueError,
                    NotImplementedError,
                    OSError,
                ) as exc:
                    raise CorruptFileError(f"corrupt ZIP entry: {info.filename}") from exc
                infos[info.filename] = info
                parts[info.filename] = b"".join(chunks)

        return parts, infos

    # ---------------------------------------------------------------- inspect

    def inspect(
        self, data: bytes, options: CleanOptions | None = None
    ) -> tuple[bytes, list[Finding], list[str]]:
        opts = options or CleanOptions()
        parts, infos = self._read_archive(data)

        kind = self._detect_kind(parts)
        if kind is None:
            raise UnsupportedFormatError("ZIP archive is not a DOCX, XLSX or PPTX document")

        findings: list[Finding] = []
        warnings: list[str] = []

        dropped: set[str] = set()

        if "docProps/core.xml" in parts:
            new_core, core_findings = self._sanitize_core(parts["docProps/core.xml"], opts.strip_dates)
            findings.extend(core_findings)
            if new_core is None:
                dropped.add("docProps/core.xml")
            else:
                parts["docProps/core.xml"] = new_core

        if "docProps/app.xml" in parts:
            new_app, app_findings = self._sanitize_app(parts["docProps/app.xml"])
            findings.extend(app_findings)
            if new_app is None:
                dropped.add("docProps/app.xml")
            else:
                parts["docProps/app.xml"] = new_app

        if "docProps/custom.xml" in parts:
            new_custom, custom_findings = self._sanitize_custom(parts["docProps/custom.xml"])
            findings.extend(custom_findings)
            if new_custom is None:
                dropped.add("docProps/custom.xml")
            else:
                parts["docProps/custom.xml"] = new_custom

        if opts.remove_thumbnails:
            for name in list(parts):
                if name.startswith("docProps/thumbnail"):
                    findings.append(
                        Finding(
                            key="office:thumbnail",
                            value=f"embedded preview image {name}",
                            category="thumbnail",
                        )
                    )
                    dropped.add(name)

        if opts.remove_comments:
            for name in list(parts):
                if self._is_comment_part(name):
                    findings.extend(self._comment_part_findings(name, parts[name], kind))
                    dropped.add(name)

        if opts.remove_embedded:
            for name in list(parts):
                if "/embeddings/" in name:
                    findings.append(
                        Finding(
                            key="office:embedded",
                            value=f"embedded object {posixpath.basename(name)}",
                            category="embedded",
                        )
                    )
                    dropped.add(name)

        transform_targets = [
            name
            for name in parts
            if name.startswith("word/")
            and name.endswith(".xml")
            and name not in dropped
            and not self._is_comment_part(name)
        ]
        for name in transform_targets:
            new_payload, part_findings = self._sanitize_word_part(
                parts[name],
                name=name,
                remove_comments=opts.remove_comments,
            )
            if part_findings:
                findings.extend(part_findings)
                parts[name] = new_payload

        dropped |= self._comment_drawing_parts(parts, dropped)
        removed_rel_ids = self._drop_relationships(parts, dropped)
        for part_name, ids in removed_rel_ids.items():
            if part_name in parts and ids:
                parts[part_name] = self._drop_dangling_references(parts[part_name], ids, name=part_name)

        for name in dropped:
            parts.pop(name, None)

        if "[Content_Types].xml" in parts:
            parts["[Content_Types].xml"] = self._prune_content_types(parts["[Content_Types].xml"], dropped)

        result = self._write_zip(parts, infos)
        if not findings:
            warnings.append("no removable Office metadata found")
        return result, findings, warnings

    # ------------------------------------------------------------- part kinds

    @staticmethod
    def _detect_kind(parts: dict[str, bytes]) -> str | None:
        if any(name.startswith("word/") for name in parts):
            return "docx"
        if any(name.startswith("xl/") for name in parts):
            return "xlsx"
        if any(name.startswith("ppt/") for name in parts):
            return "pptx"
        return None

    @staticmethod
    def _is_comment_part(name: str) -> bool:
        lowered = name.lower()
        if lowered.startswith(("word/comments", "word/people")):
            return True
        if lowered.startswith(("xl/comments", "xl/persons/", "xl/threadedcomments/")):
            return True
        return lowered.startswith(("ppt/comments/", "ppt/commentauthors"))

    @staticmethod
    def _comment_part_findings(name: str, payload: bytes, kind: str) -> list[Finding]:
        authors: list[str] = []
        text: list[str] = []
        try:
            root = _parse(payload)
        except ET.ParseError as exc:
            raise CorruptFileError(f"{name} is not well formed: {exc}") from exc
        for element in root.iter():
            tag = localname(element.tag)
            if tag in {"author", "initials"} and element.text and element.text.strip():
                authors.append(element.text.strip())
            elif tag == "t" and element.text and element.text.strip():
                text.append(element.text.strip())
            # Word stores the comment author in a w:author attribute, not in a
            # child element; missing it would leave a personal name out of the
            # audit report even though the part is deleted.
            for attribute in ("author", "userId"):
                raw = element.attrib.get(qname(NS_W, attribute)) or element.attrib.get(attribute)
                if raw and raw.strip():
                    authors.append(raw.strip())
        findings = [
            Finding(
                key=f"office:{name}",
                value=f"comments part ({kind})",
                category="comment",
            )
        ]
        for author in dict.fromkeys(authors):
            findings.append(Finding(key="office:comment_author", value=shorten(author), category="identity"))
        for snippet in list(dict.fromkeys(text))[:5]:
            findings.append(Finding(key="office:comment_text", value=shorten(snippet), category="comment"))
        return findings

    # -------------------------------------------------------------- docProps

    @staticmethod
    def _sanitize_core(payload: bytes, strip_dates: bool) -> tuple[bytes | None, list[Finding]]:
        try:
            root = _parse(payload)
        except ET.ParseError as exc:
            raise CorruptFileError(f"docProps/core.xml is not well formed: {exc}") from exc
        findings: list[Finding] = []
        keep = set() if strip_dates else {qname(NS_DCTERMS, "created"), qname(NS_DCTERMS, "modified")}
        for child in list(root):
            if child.tag in keep:
                continue
            name = localname(child.tag)
            value = (child.text or "").strip()
            category = CORE_CATEGORIES.get(name, "other")
            findings.append(
                Finding(key=f"core:{name}", value=shorten(value) if value else "(empty)", category=category)
            )
            root.remove(child)
        if len(root) == 0:
            return None, findings
        return serialize(root), findings

    @staticmethod
    def _sanitize_app(payload: bytes) -> tuple[bytes | None, list[Finding]]:
        try:
            root = _parse(payload)
        except ET.ParseError as exc:
            raise CorruptFileError(f"docProps/app.xml is not well formed: {exc}") from exc
        findings: list[Finding] = []
        for child in list(root):
            if namespace_of(child.tag) != NS_EXT:
                continue
            name = localname(child.tag)
            if name in APP_ALLOWED:
                continue
            value = (child.text or "").strip()
            category = APP_CATEGORIES.get(name, "other")
            findings.append(
                Finding(key=f"app:{name}", value=shorten(value) if value else "(empty)", category=category)
            )
            root.remove(child)
        if len(root) == 0:
            return None, findings
        return serialize(root), findings

    @staticmethod
    def _sanitize_custom(payload: bytes) -> tuple[bytes | None, list[Finding]]:
        try:
            root = _parse(payload)
        except ET.ParseError as exc:
            raise CorruptFileError(f"docProps/custom.xml is not well formed: {exc}") from exc
        findings: list[Finding] = []
        for prop in list(root):
            name = prop.get("name", localname(prop.tag))
            value = ""
            for child in prop:
                value = (child.text or "").strip()
                break
            findings.append(
                Finding(
                    key=f"custom:{name}",
                    value=shorten(value) if value else "(empty)",
                    category=classify_custom_property(name),
                )
            )
            root.remove(prop)
        if len(root) == 0:
            return None, findings
        return serialize(root), findings

    # --------------------------------------------------------------- Word XML

    def _sanitize_word_part(
        self,
        payload: bytes,
        name: str = "word/document.xml",
        remove_comments: bool = True,
    ) -> tuple[bytes, list[Finding]]:
        try:
            root = _parse(payload)
        except ET.ParseError as exc:
            raise CorruptFileError(f"{name} is not well formed: {exc}") from exc
        findings: list[Finding] = []
        stats = {"deleted": 0, "inserted": 0, "comments": 0, "rsids": 0, "changes": 0}
        change_authors: list[str] = []

        def walk(parent: ET.Element) -> None:
            for child in list(parent):
                tag = child.tag
                name = localname(tag)
                if namespace_of(tag) != NS_W:
                    walk(child)
                    continue
                if name in {"commentReference", "commentRangeStart", "commentRangeEnd"}:
                    if remove_comments:
                        stats["comments"] += 1
                        parent.remove(child)
                    continue
                if name in W_DROP:
                    if name == "del":
                        stats["deleted"] += 1
                        findings.append(
                            Finding(
                                key="word:tracked-deletion",
                                value=shorten(
                                    "".join(
                                        t.text or "" for t in child.iter() if localname(t.tag) == "delText"
                                    )
                                )
                                or "deleted revision",
                                category="revision",
                            )
                        )
                    elif name == "rsids":
                        stats["rsids"] += 1
                    else:
                        # A tracked formatting/property change: the old property
                        # snapshot is dropped, but its author is worth reporting.
                        if name.endswith("PrChange") or name == "numberingChange":
                            stats["changes"] += 1
                            author = child.attrib.get(qname(NS_W, "author"))
                            if author and author.strip():
                                change_authors.append(author.strip())
                    parent.remove(child)
                    continue
                if name in W_UNWRAP:
                    index = list(parent).index(child)
                    parent.remove(child)
                    walk(child)
                    for offset, grandchild in enumerate(list(child)):
                        parent.insert(index + offset, grandchild)
                    if name in {"ins", "moveTo"}:
                        stats["inserted"] += 1
                    continue
                self._strip_revision_attributes(child)
                walk(child)

        self._strip_revision_attributes(root)
        walk(root)

        if stats["comments"]:
            findings.append(
                Finding(
                    key="word:comment-anchor",
                    value=f"{stats['comments']} comment reference(s)",
                    category="comment",
                )
            )
        if stats["rsids"]:
            findings.append(
                Finding(
                    key="word:rsid",
                    value=f"{stats['rsids']} revision-save id block(s)",
                    category="revision",
                )
            )
        if stats["changes"]:
            findings.append(
                Finding(
                    key="word:formatting-change",
                    value=f"{stats['changes']} tracked formatting change(s)",
                    category="revision",
                )
            )
        for author in dict.fromkeys(change_authors):
            findings.append(Finding(key="word:change_author", value=shorten(author), category="identity"))
        if not findings:
            return payload, []
        return serialize(root), findings

    @staticmethod
    def _strip_revision_attributes(element: ET.Element) -> None:
        for attr in list(element.attrib):
            if namespace_of(attr) != NS_W:
                continue
            name = localname(attr)
            if name.startswith("rsid") or name in {"author", "date", "userId"}:
                del element.attrib[attr]

    # ------------------------------------------------------- relationships

    @staticmethod
    def _rels_name_for(part: str) -> str:
        directory, filename = posixpath.split(part)
        return (
            posixpath.join(directory, "_rels", filename + ".rels")
            if directory
            else posixpath.join("_rels", filename + ".rels")
        )

    @staticmethod
    def _resolve_target(rels_name: str, target: str) -> str:
        if target.startswith("/"):
            return target.lstrip("/")
        source_dir = posixpath.dirname(posixpath.dirname(rels_name))
        return posixpath.normpath(posixpath.join(source_dir, target))

    def _comment_drawing_parts(self, parts: dict[str, bytes], dropped: set[str]) -> set[str]:
        """Return legacy VML drawing parts that only exist to render comments.

        A sheet that owns a comments part also owns a ``vmlDrawing`` part holding
        the comment shapes.  Dropping the comments while keeping the drawing
        would leave the comment text in the file.
        """
        extra: set[str] = set()
        for rels_name, payload in parts.items():
            if not rels_name.endswith(".rels"):
                continue
            try:
                root = _parse(payload)
            except ET.ParseError as exc:
                raise CorruptFileError(f"{rels_name} is not well formed: {exc}") from exc
            rels = [rel for rel in root if localname(rel.tag) == "Relationship"]
            has_comments = False
            drawings: list[str] = []
            for rel in rels:
                rel_type = rel.get("Type", "")
                target = rel.get("Target", "")
                resolved = self._resolve_target(rels_name, target)
                if rel_type.endswith(REL_TYPES_COMMENTS) and resolved in dropped:
                    has_comments = True
                elif rel_type.endswith("/vmlDrawing"):
                    drawings.append(resolved)
            if has_comments:
                extra.update(drawings)
        return extra

    def _drop_relationships(self, parts: dict[str, bytes], dropped: set[str]) -> dict[str, list[str]]:
        """Remove relationships pointing at dropped parts.

        Returns ``{source_part: [removed rId, ...]}`` so dangling references in the
        source XML can be removed afterwards.
        """
        removed: dict[str, list[str]] = {}
        for rels_name in list(parts):
            if not rels_name.endswith(".rels"):
                continue
            try:
                root = _parse(parts[rels_name])
            except ET.ParseError as exc:
                raise CorruptFileError(f"{rels_name} is not well formed: {exc}") from exc
            source_part = posixpath.join(
                posixpath.dirname(posixpath.dirname(rels_name)),
                posixpath.basename(rels_name)[: -len(".rels")],
            )
            changed = False
            for rel in list(root):
                if localname(rel.tag) != "Relationship":
                    continue
                if rel.get("TargetMode") == "External":
                    continue
                target = rel.get("Target", "")
                resolved = self._resolve_target(rels_name, target)
                if resolved in dropped:
                    rid = rel.get("Id", "")
                    if rid:
                        removed.setdefault(source_part, []).append(rid)
                    root.remove(rel)
                    changed = True
            if changed:
                parts[rels_name] = serialize(root)
        return removed

    @staticmethod
    def _drop_dangling_references(payload: bytes, rel_ids: list[str], name: str = "document.xml") -> bytes:
        try:
            root = _parse(payload)
        except ET.ParseError as exc:
            raise CorruptFileError(f"{name} is not well formed: {exc}") from exc
        parents = {child: parent for parent in root.iter() for child in parent}
        wanted = set(rel_ids)
        changed = False
        for element in list(root.iter()):
            rid = None
            for attr in (qname(NS_R, "id"), qname(NS_R, "embed"), qname(NS_R, "link")):
                if attr in element.attrib:
                    rid = element.attrib[attr]
                    break
            if rid is None or rid not in wanted:
                continue
            parent = parents.get(element)
            if parent is None:
                continue
            victim = element
            grandparent = parents.get(parent)
            if localname(parent.tag) in {"object", "pict", "drawing"}:
                victim = parent
            elif grandparent is not None and localname(grandparent.tag) in {"object", "pict"}:
                victim = grandparent
            holder = parents.get(victim)
            if holder is not None:
                holder.remove(victim)
                changed = True
        if not changed:
            return payload
        return serialize(root)

    @staticmethod
    def _prune_content_types(payload: bytes, dropped: set[str]) -> bytes:
        try:
            root = _parse(payload)
        except ET.ParseError as exc:
            raise CorruptFileError(f"[Content_Types].xml is not well formed: {exc}") from exc
        names = {"/" + name for name in dropped}
        changed = False
        for element in list(root):
            part_name = element.get("PartName")
            if part_name and part_name in names:
                root.remove(element)
                changed = True
        if not changed:
            return payload
        return serialize(root)

    # ---------------------------------------------------------- zip assembly

    @staticmethod
    def _write_zip(parts: dict[str, bytes], infos: dict[str, zipfile.ZipInfo]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in parts.items():
                source = infos.get(name)
                date_time = source.date_time if source is not None else (1980, 1, 1, 0, 0, 0)
                info = zipfile.ZipInfo(name, date_time=date_time)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = source.external_attr if source is not None else 0o600 << 16
                archive.writestr(info, payload)
        return buffer.getvalue()
