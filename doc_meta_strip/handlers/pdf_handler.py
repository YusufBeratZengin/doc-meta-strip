"""PDF sanitizer: Info dictionary, XMP packet, embedded files and thumbnails.

``pypdf`` rewrites the document object graph; content streams are copied
verbatim so text, images and form fields stay intact.  After stripping, the
writer performs a reachability sweep and blanks every unreachable object, so
no orphaned copy of the removed metadata survives anywhere in the file bytes.
"""

from __future__ import annotations

import io
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    IndirectObject,
    NameObject,
    StreamObject,
)

from ..base import (
    CleanOptions,
    CorruptFileError,
    Finding,
    Handler,
    PasswordRequiredError,
    WrongPasswordError,
    shorten,
)
from .image_handler import xmp_findings

__all__ = ["PdfHandler"]

INFO_CATEGORIES = {
    "/Author": "identity",
    "/Creator": "software",
    "/Producer": "software",
    "/Company": "organization",
    "/Title": "description",
    "/Subject": "description",
    "/Keywords": "description",
    "/CreationDate": "timestamp",
    "/ModDate": "timestamp",
    "/LastModifiedBy": "identity",
    "/SourceApplication": "software",
}

#: Info keys that survive unless ``--strip-dates`` is given.
KEPT_DATE_KEYS = ("/CreationDate", "/ModDate")


class PdfHandler(Handler):
    name = "pdf"
    extensions = (".pdf",)

    def sniff(self, data: bytes) -> bool:
        return b"%PDF-" in data[:1024]

    # ---------------------------------------------------------------- inspect

    def inspect(
        self, data: bytes, options: CleanOptions | None = None
    ) -> tuple[bytes, list[Finding], list[str]]:
        opts = options or CleanOptions()
        reader = self._open(data, opts.password)
        warnings: list[str] = []
        findings, kept_info = self._collect_findings(reader, opts, warnings)
        if reader.is_encrypted:
            warnings.append("input was encrypted; the cleaned copy is not password protected")
        if not findings:
            return data, findings, warnings
        cleaned = self._rewrite(reader, opts, kept_info)
        return cleaned, findings, warnings

    @staticmethod
    def _open(data: bytes, password: str | None) -> PdfReader:
        try:
            reader = PdfReader(io.BytesIO(data))
        except PdfReadError as exc:
            raise CorruptFileError(f"not a readable PDF: {exc}") from exc
        if reader.is_encrypted:
            if not password:
                raise PasswordRequiredError("PDF is password protected; pass --password to clean it")
            try:
                result = reader.decrypt(password)
            except Exception as exc:  # pypdf raises several error types here
                raise WrongPasswordError("PDF password was rejected") from exc
            if result == 0:
                raise WrongPasswordError("PDF password was rejected")
        return reader

    def _collect_findings(
        self,
        reader: PdfReader,
        opts: CleanOptions,
        warnings: list[str] | None = None,
    ) -> tuple[list[Finding], dict[str, Any]]:
        warn_list = warnings if warnings is not None else []
        findings: list[Finding] = []
        kept_info: dict[str, Any] = {}
        info = self._info_dict(reader)
        if info:
            removed_any = False
            for key, value in info.items():
                name = str(key)
                if value is None or str(value).strip() == "":
                    continue
                if name in KEPT_DATE_KEYS and not opts.strip_dates:
                    kept_info[name] = value
                    continue
                category = INFO_CATEGORIES.get(name, "other")
                findings.append(Finding(key=f"pdf:Info{name}", value=shorten(str(value)), category=category))
                removed_any = True
            if removed_any:
                findings.append(
                    Finding(
                        key="pdf:Info",
                        value="document information dictionary",
                        category="other",
                    )
                )

        root = self._root(reader)
        if root is not None and "/Metadata" in root:
            packet = b""
            try:
                raw: Any = root["/Metadata"]
                stream = raw.get_object() if isinstance(raw, IndirectObject) else raw
                if isinstance(stream, StreamObject):
                    packet = stream.get_data()
            except Exception as exc:
                warn_list.append(f"damaged PDF XMP stream removed without parsing: {exc}")
                packet = b""
            findings.extend(
                xmp_findings(packet)
                if packet
                else [Finding(key="pdf:XMP", value="XMP metadata stream", category="other")]
            )

        if opts.remove_embedded:
            findings.extend(self._attachment_findings(reader, root))

        if opts.remove_thumbnails:
            try:
                for number, page in enumerate(reader.pages, start=1):
                    if "/Thumb" in page:
                        findings.append(
                            Finding(
                                key=f"pdf:thumb:page{number}",
                                value="page preview thumbnail",
                                category="thumbnail",
                            )
                        )
            except Exception as exc:
                raise CorruptFileError(f"damaged PDF page tree while inspecting thumbnails: {exc}") from exc

        if opts.remove_embedded:
            try:
                for number, page in enumerate(reader.pages, start=1):
                    annots = page.get("/Annots")
                    if isinstance(annots, IndirectObject):
                        annots = annots.get_object()
                    if not isinstance(annots, ArrayObject):
                        continue
                    for ref in annots:
                        annotation = ref.get_object() if isinstance(ref, IndirectObject) else ref
                        if not isinstance(annotation, DictionaryObject):
                            continue
                        subtype = str(annotation.get("/Subtype", ""))
                        if subtype in {"/FileAttachment", "/Movie", "/Sound"}:
                            kind = subtype.lstrip("/")
                            findings.append(
                                Finding(
                                    key=f"pdf:annotation:{kind}",
                                    value=f"page {number} {kind} annotation",
                                    category="embedded",
                                )
                            )
            except Exception as exc:
                raise CorruptFileError(f"damaged PDF page tree while inspecting annotations: {exc}") from exc

        if root is not None and "/PieceInfo" in root:
            findings.append(Finding(key="pdf:PieceInfo", value="application private data", category="other"))
        return findings, kept_info

    @staticmethod
    def _info_dict(reader: PdfReader) -> DictionaryObject | None:
        try:
            info = reader.metadata
        except Exception as exc:
            raise CorruptFileError(f"damaged PDF Info dictionary: {exc}") from exc
        if not info:
            return None
        return DictionaryObject({NameObject(str(k)): v for k, v in info.items()})

    @staticmethod
    def _attachment_findings(reader: PdfReader, root: Any) -> list[Finding]:
        findings: list[Finding] = []
        try:
            for name in reader.attachments:
                findings.append(Finding(key="pdf:attachment", value=shorten(str(name)), category="embedded"))
        except Exception as exc:
            raise CorruptFileError(f"damaged PDF attachment tree: {exc}") from exc
        if root is not None and "/Names" in root:
            try:
                names = root["/Names"]
                if isinstance(names, IndirectObject):
                    names = names.get_object()
            except Exception as exc:
                raise CorruptFileError(f"damaged PDF /Names dictionary: {exc}") from exc
            if isinstance(names, DictionaryObject):
                if "/EmbeddedFiles" in names and not findings:
                    findings.append(
                        Finding(
                            key="pdf:embedded-files",
                            value="embedded file name tree",
                            category="embedded",
                        )
                    )
                if "/JavaScript" in names:
                    findings.append(
                        Finding(
                            key="pdf:javascript",
                            value="embedded JavaScript name tree",
                            category="embedded",
                        )
                    )
        return findings

    @staticmethod
    def _root(reader: PdfReader) -> DictionaryObject | None:
        try:
            raw: Any = reader.trailer["/Root"]
            root = raw.get_object() if isinstance(raw, IndirectObject) else raw
            if isinstance(root, DictionaryObject):
                return root
        except Exception as exc:
            raise CorruptFileError(f"damaged PDF trailer /Root: {exc}") from exc
        return None

    # ---------------------------------------------------------------- rewrite

    def _rewrite(self, reader: PdfReader, opts: CleanOptions, kept_info: dict[str, Any]) -> bytes:
        writer = PdfWriter(clone_from=reader)
        writer.metadata = None
        root = writer.root_object
        root.pop("/Metadata", None)
        root.pop("/PieceInfo", None)

        if kept_info:
            writer.add_metadata(kept_info)

        if opts.remove_embedded:
            names_ref = root.get("/Names")
            if names_ref is not None:
                names = names_ref.get_object() if isinstance(names_ref, IndirectObject) else names_ref
                if isinstance(names, DictionaryObject):
                    names.pop("/EmbeddedFiles", None)
                    names.pop("/JavaScript", None)
                    if len(names) == 0:
                        root.pop("/Names", None)

        for page in writer.pages:
            self._strip_page(page, opts)

        _collect_garbage(writer)
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    @staticmethod
    def _strip_page(page: Any, opts: CleanOptions) -> None:
        if opts.remove_thumbnails:
            page.pop("/Thumb", None)
        if not opts.remove_embedded:
            return
        annots = page.get("/Annots")
        if annots is None:
            return
        if isinstance(annots, IndirectObject):
            annots = annots.get_object()
        if not isinstance(annots, ArrayObject):
            return
        keep: list[Any] = []
        for ref in annots:
            annotation = ref.get_object() if isinstance(ref, IndirectObject) else ref
            subtype = ""
            if isinstance(annotation, DictionaryObject):
                subtype = str(annotation.get("/Subtype", ""))
            if subtype in {"/FileAttachment", "/Movie", "/Sound"}:
                continue
            keep.append(ref)
        if len(keep) == len(annots):
            return
        if not keep:
            page.pop("/Annots", None)
            return
        replacement = ArrayObject()
        for ref in keep:
            replacement.append(ref)
        page[NameObject("/Annots")] = replacement


def _collect_garbage(writer: PdfWriter) -> int:
    """Blank object slots that are no longer reachable from the catalog.

    pypdf serialises every entry of ``writer._objects``; without this sweep a
    removed XMP packet or attachment would still be written to disk as an
    unreferenced (but perfectly readable) object.
    """
    objects = writer._objects  # noqa: SLF001 - pypdf exposes no public API for this
    reachable: set[int] = set()

    def visit(node: Any) -> None:
        if isinstance(node, IndirectObject):
            idnum = node.idnum
            if idnum in reachable or idnum < 1 or idnum > len(objects):
                return
            reachable.add(idnum)
            visit(objects[idnum - 1])
        elif isinstance(node, ArrayObject):
            for item in node:
                visit(item)
        elif isinstance(node, DictionaryObject):
            for _key, value in node.items():
                visit(value)

    info = getattr(writer, "_info", None)
    for index, obj in enumerate(objects, start=1):
        if obj is writer.root_object or (info is not None and obj is info):
            reachable.add(index)

    visit(writer.root_object)
    if info is not None:
        visit(info)

    freed = 0
    for index in range(1, len(objects) + 1):
        if index not in reachable:
            objects[index - 1] = None
            freed += 1
    return freed
