"""Metadata removal for the picture formats Office packages embed besides JPEG/PNG/WebP.

GIF, SVG and EMF are common inside ``word/media``, ``xl/media`` and
``ppt/media`` (icons, pasted charts, clip art).  Each cleaner is idempotent: it
reports a finding only for bytes it actually removes, so a cleaned picture scans
clean on the next pass.  WMF carries no free-form metadata and is passed
through untouched by the caller.
"""

from __future__ import annotations

import io
import struct
from xml.etree import ElementTree as ET

from ..base import CorruptFileError, Finding

__all__ = ["clean_emf", "clean_gif", "clean_svg", "sniff_kind"]

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"
XHTML_NS = "http://www.w3.org/1999/xhtml"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
# Visible content may live in foreignObject; editor/metadata namespaces are dropped.
_KEPT_NAMESPACES = {SVG_NS, XLINK_NS, XML_NS, XHTML_NS, MATHML_NS, ""}
_METADATA_TAGS = {"metadata", "title", "desc"}
_KEPT_GIF_APPS = {b"NETSCAPE2.0", b"ANIMEXTS1.0"}  # loop count, needed for playback
_EMF_SIGNATURE = b" EMF"
_EMF_N_DESCRIPTION = 0x3C
_EMF_MIN_HEADER = 0x58  # smallest ENHMETAHEADER (no pixel format / OpenGL extension)
_WMF_MAGIC = (b"\xd7\xcd\xc6\x9a", b"\x01\x00\x09\x00", b"\x02\x00\x09\x00")


def sniff_kind(name: str, data: bytes) -> str | None:
    """Return ``gif``, ``svg``, ``emf``, ``wmf``, ``tiff`` or ``None``."""
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"\x01\x00\x00\x00" and data[40:44] == _EMF_SIGNATURE:
        return "emf"
    if data[:4] in _WMF_MAGIC:
        return "wmf"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if name.lower().endswith(".svg") or (head.startswith((b"<svg", b"<?xml")) and b"<svg" in head):
        return "svg"
    return None


def _skip_sub_blocks(data: bytes, pos: int) -> int:
    while True:
        size = data[pos]
        pos += 1 + size
        if size == 0:
            return pos


def clean_gif(data: bytes, label: str) -> tuple[bytes, list[Finding]]:
    """Drop comment and non-playback application extensions plus trailing bytes."""
    out = bytearray(data[:6])
    removed: list[str] = []
    try:
        packed = data[10]
        pos = 13
        if packed & 0x80:
            pos += 3 * (1 << ((packed & 7) + 1))
        out += data[6:pos]
        while True:
            marker = data[pos]
            if marker == 0x3B:  # trailer
                out.append(0x3B)
                pos += 1
                break
            if marker == 0x21:
                ext = data[pos + 1]
                head = 2 + (1 + data[pos + 2] if ext in (0xFF, 0x01) else 0)
                end = _skip_sub_blocks(data, pos + head)
                keep = True
                if ext == 0xFE:
                    keep = False
                    removed.append("comment")
                elif ext == 0xFF:
                    app = bytes(data[pos + 3 : pos + 3 + data[pos + 2]])
                    if app not in _KEPT_GIF_APPS:
                        keep = False
                        removed.append(f"application extension {app.decode('latin-1')!r}")
                if keep:
                    out += data[pos:end]
                pos = end
            elif marker == 0x2C:
                flags = data[pos + 9]
                header_end = pos + 10
                if flags & 0x80:
                    header_end += 3 * (1 << ((flags & 7) + 1))
                end = _skip_sub_blocks(data, header_end + 1)
                out += data[pos:end]
                pos = end
            else:
                raise CorruptFileError(f"unexpected GIF block 0x{marker:02x}")
    except IndexError as exc:
        raise CorruptFileError("truncated GIF") from exc
    findings = [
        Finding(key="gif:extension", value=f"{label}: {item}", category="comment") for item in removed
    ]
    if pos < len(data):
        findings.append(
            Finding(key="gif:trailing_data", value=f"{label}: {len(data) - pos} bytes", category="embedded")
        )
    if not findings:
        return data, []
    return bytes(out), findings


def _ns(tag: object) -> str:
    return tag[1:].split("}", 1)[0] if isinstance(tag, str) and tag.startswith("{") else ""


def _prune_svg(element: ET.Element, removed: list[str]) -> None:
    for attribute in list(element.attrib):
        if _ns(attribute) not in _KEPT_NAMESPACES:
            del element.attrib[attribute]
            removed.append("editor attribute")
    for child in list(element):
        tag = child.tag if isinstance(child.tag, str) else ""
        local = tag.rsplit("}", 1)[-1]
        in_svg = _ns(tag) in (SVG_NS, "")
        if _ns(tag) not in _KEPT_NAMESPACES or (in_svg and local in _METADATA_TAGS):
            element.remove(child)
            removed.append(f"element {local}")
        else:
            _prune_svg(child, removed)


def clean_svg(data: bytes, label: str) -> tuple[bytes, list[Finding]]:
    """Remove ``metadata``/``title``/``desc``, comments and editor-specific markup."""
    try:
        for _event, (prefix, uri) in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
            if prefix and not prefix.startswith("ns"):
                ET.register_namespace(prefix, uri)
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError) as exc:
        raise CorruptFileError(f"SVG is not well formed: {exc}") from exc
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    removed: list[str] = []
    _prune_svg(root, removed)
    if b"<!--" in data:  # ElementTree drops comments when it re-serialises
        removed.append("comment")
    if not removed:
        return data, []
    body = ET.tostring(root, encoding="unicode")
    cleaned = ('<?xml version="1.0" encoding="UTF-8"?>\n' + body).encode("utf-8")
    findings = [
        Finding(key="svg:metadata", value=f"{label}: {item}", category="description")
        for item in dict.fromkeys(removed)
    ]
    return cleaned, findings


def clean_emf(data: bytes, label: str) -> tuple[bytes, list[Finding]]:
    """Blank the header description (application / document title) in place.

    ``ENHMETAHEADER`` ([MS-EMF] 2.2.9): ``nDescription`` is at 0x3C and
    ``offDescription`` at 0x40; the bytes at 0x2C/0x30 are ``nVersion`` and
    ``nBytes`` and must never be touched.
    """
    if len(data) < _EMF_MIN_HEADER:
        raise CorruptFileError("truncated EMF header")
    chars, offset = struct.unpack_from("<II", data, _EMF_N_DESCRIPTION)
    if chars == 0:
        return data, []
    end = offset + 2 * chars
    if offset < _EMF_MIN_HEADER or end > len(data):
        raise CorruptFileError("EMF description is out of bounds")
    out = bytearray(data)
    out[offset:end] = bytes(end - offset)
    struct.pack_into("<II", out, _EMF_N_DESCRIPTION, 0, 0)
    return bytes(out), [
        Finding(key="emf:description", value=f"{label}: header description", category="description")
    ]
