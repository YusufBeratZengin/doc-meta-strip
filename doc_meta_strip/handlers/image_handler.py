"""Lossless metadata removal for JPEG, PNG and WebP.

Nothing in this module decodes or re-encodes pixels.  JPEG segments, PNG
chunks and WebP RIFF chunks are copied byte for byte; only the containers that
carry metadata are dropped.  Pillow is used exclusively to *read* tag values so
the audit report can say what was inside.
"""

from __future__ import annotations

import io
import struct
import zlib
from typing import Any

from ..base import CorruptFileError, Finding, Handler, shorten

__all__ = ["ImageHandler"]

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: PNG chunks that carry human readable or machine readable metadata.
PNG_METADATA_CHUNKS = frozenset({b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME", b"dSIG"})

#: APPn markers that must survive: JFIF (APP0), ICC colour profile (APP2) and
#: the Adobe colour transform marker (APP14) which some CMYK decoders require.
JPEG_KEPT_APP_MARKERS = frozenset({0xE0, 0xE2, 0xEE})

JPEG_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})

EXIF_TAGS: dict[int, tuple[str, str]] = {
    0x010E: ("ImageDescription", "description"),
    0x010F: ("Make", "device"),
    0x0110: ("Model", "device"),
    0x0112: ("Orientation", "other"),
    0x0131: ("Software", "software"),
    0x0132: ("DateTime", "timestamp"),
    0x013B: ("Artist", "identity"),
    0x8298: ("Copyright", "identity"),
    0x9003: ("DateTimeOriginal", "timestamp"),
    0x9004: ("DateTimeDigitized", "timestamp"),
    0x927C: ("MakerNote", "other"),
    0x9286: ("UserComment", "identity"),
    0x9C9B: ("XPTitle", "description"),
    0x9C9C: ("XPComment", "description"),
    0x9C9D: ("XPAuthor", "identity"),
    0x9C9E: ("XPKeywords", "description"),
    0x9C9F: ("XPSubject", "description"),
    0xA430: ("CameraOwnerName", "identity"),
    0xA431: ("BodySerialNumber", "device"),
    0xA433: ("LensMake", "device"),
    0xA434: ("LensModel", "device"),
}

GPS_TAG_NAMES = {
    0: "GPSVersionID",
    1: "GPSLatitudeRef",
    2: "GPSLatitude",
    3: "GPSLongitudeRef",
    4: "GPSLongitude",
    5: "GPSAltitudeRef",
    6: "GPSAltitude",
    7: "GPSTimeStamp",
    29: "GPSDateStamp",
}

#: Local names in an XMP packet that are worth reporting.
XMP_INTERESTING = {
    "creator": "identity",
    "Author": "identity",
    "AuthorsPosition": "identity",
    "lastModifiedBy": "identity",
    "Credit": "identity",
    "CreatorTool": "software",
    "Software": "software",
    "Producer": "software",
    "Company": "organization",
    "Manager": "identity",
    "History": "revision",
    "DerivedFrom": "revision",
    "DocumentID": "other",
}

PNG_TEXT_CATEGORIES = {
    "parameters": "text",
    "prompt": "text",
    "workflow": "text",
    "software": "software",
    "author": "identity",
    "artist": "identity",
    "copyright": "identity",
    "comment": "description",
    "description": "description",
    "title": "description",
    "creation time": "timestamp",
    "date:create": "timestamp",
    "date:modify": "timestamp",
}


def _localname(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[1]
    return tag


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _dms(value: Any) -> float | None:
    """Convert an EXIF ``(deg, min, sec)`` triple into decimal degrees."""
    if value is None:
        return None
    try:
        parts = list(value)
    except TypeError:
        return None
    if len(parts) != 3:
        return None
    degrees = _as_float(parts[0])
    minutes = _as_float(parts[1])
    seconds = _as_float(parts[2])
    if degrees is None or minutes is None or seconds is None:
        return None
    return degrees + minutes / 60.0 + seconds / 3600.0


def gps_summary(gps: dict[Any, Any]) -> str | None:
    """Render a readable ``lat, lon`` string from an EXIF GPS IFD."""
    lat = _dms(gps.get(2))
    lon = _dms(gps.get(4))
    if lat is None or lon is None:
        return None
    lat_ref = str(gps.get(1, "N")).strip().upper()[:1] or "N"
    lon_ref = str(gps.get(3, "E")).strip().upper()[:1] or "E"
    if lat_ref == "S":
        lat = -lat
    if lon_ref == "W":
        lon = -lon
    return f"{lat:.6f}, {lon:.6f}"


def _stringify(value: Any) -> str | None:
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8", "replace")
        except Exception:  # pragma: no cover - decode with replace cannot fail
            return None
        text = text.replace("\x00", " ")
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    text = shorten(text.strip())
    return text or None


def exif_findings(exif: Any) -> list[Finding]:
    """Turn a Pillow ``Exif`` object into report findings."""
    findings: list[Finding] = []
    if exif is None:
        return findings
    try:
        items = list(exif.items())
    except Exception:  # pragma: no cover - defensive
        return findings
    for tag, value in items:
        known = EXIF_TAGS.get(tag)
        if known is None:
            continue
        key, category = known
        if key == "Orientation":
            continue
        text = _stringify(value)
        if text is None:
            continue
        findings.append(Finding(key=f"exif:{key}", value=text, category=category))
    try:
        gps = exif.get_ifd(0x8825)
    except Exception:  # pragma: no cover - malformed GPS IFD
        gps = {}
    if gps:
        summary = gps_summary(gps)
        findings.append(
            Finding(
                key="exif:GPS",
                value=summary or f"{len(gps)} GPS tag(s)",
                category="gps",
            )
        )
    return findings


def read_exif(data: bytes) -> Any:
    """Return a Pillow ``Exif`` object for ``data`` or ``None`` if unreadable."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a hard dependency
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.getexif()
    except Exception:
        return None


def xmp_findings(packet: bytes) -> list[Finding]:
    """Extract reportable values from an XMP packet."""
    import xml.etree.ElementTree as ET

    text = packet.decode("utf-8", "replace")
    start = text.find("<")
    if start > 0:
        text = text[start:]
    end = text.rfind(">")
    if end != -1:
        text = text[: end + 1]
    findings: list[Finding] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return [Finding(key="xmp", value="unparsable XMP packet", category="other")]
    for element in root.iter():
        name = _localname(element.tag)
        category = XMP_INTERESTING.get(name)
        if category is None:
            continue
        value = (element.text or "").strip()
        if not value and len(element):
            value = " ".join((child.text or "").strip() for child in element.iter() if child.text)
        if not value:
            for attr_name, attr_value in element.attrib.items():
                if _localname(attr_name) in XMP_INTERESTING:
                    value = str(attr_value).strip()
                    break
        if value:
            findings.append(Finding(key=f"xmp:{name}", value=shorten(value), category=category))
    if not findings:
        findings.append(Finding(key="xmp", value="XMP packet present", category="other"))
    return findings


class ImageHandler(Handler):
    name = "image"
    extensions = (".jpg", ".jpeg", ".jpe", ".png", ".webp")

    def sniff(self, data: bytes) -> bool:
        return data.startswith((b"\xff\xd8", PNG_SIGNATURE)) or (
            data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        )

    def inspect(self, data: bytes, options: Any = None) -> tuple[bytes, list[Finding], list[str]]:
        if data.startswith(b"\xff\xd8"):
            return self._strip_jpeg(data)
        if data.startswith(PNG_SIGNATURE):
            return self._strip_png(data)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return self._strip_webp(data)
        raise CorruptFileError("not a JPEG, PNG or WebP file")

    # ------------------------------------------------------------------ JPEG

    def _strip_jpeg(self, data: bytes) -> tuple[bytes, list[Finding], list[str]]:
        findings: list[Finding] = []
        warnings: list[str] = []
        out = bytearray(data[:2])
        index = 2
        size = len(data)
        saw_metadata_segment = False
        saw_scan = False
        while index < size:
            if data[index] != 0xFF:
                # Not a marker: preserve the remainder untouched.
                out += data[index:]
                break
            segment_start = index
            cursor = index + 1
            while cursor < size and data[cursor] == 0xFF:
                cursor += 1
            if cursor >= size:
                raise CorruptFileError("truncated JPEG marker stream")
            marker = data[cursor]
            if marker == 0xD9:  # EOI
                if not saw_scan:
                    raise CorruptFileError("JPEG has no image data (missing SOS)")
                out += data[segment_start:]
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD8:
                out += data[segment_start : cursor + 1]
                index = cursor + 1
                continue
            if cursor + 2 >= size:
                raise CorruptFileError("truncated JPEG segment length")
            length = int.from_bytes(data[cursor + 1 : cursor + 3], "big")
            if length < 2:
                raise CorruptFileError("invalid JPEG segment length")
            segment_end = cursor + 1 + length
            if segment_end > size:
                raise CorruptFileError("JPEG segment runs past end of file")
            segment = data[segment_start:segment_end]
            if marker == 0xDA:  # SOS: entropy coded data follows verbatim
                saw_scan = True
                out += segment
                out += data[segment_end:]
                break
            keep = True
            if 0xE0 <= marker <= 0xEF and marker not in JPEG_KEPT_APP_MARKERS:
                keep = False
                saw_metadata_segment = True
                findings.extend(self._jpeg_app_findings(marker, segment))
            elif marker == 0xFE:
                keep = False
                saw_metadata_segment = True
                payload = segment[4:]
                findings.append(
                    Finding(
                        key="jpeg:COM",
                        value=_stringify(payload) or "JPEG comment",
                        category="comment",
                    )
                )
            if keep:
                out += segment
            index = segment_end
        else:  # loop finished without SOS/EOI
            raise CorruptFileError("JPEG has no image data (missing SOS)")

        findings.extend(exif_findings(read_exif(data)))
        if saw_metadata_segment and not findings:
            findings.append(Finding(key="jpeg:APP", value="APP metadata segment", category="other"))
        if not findings:
            warnings.append("no removable metadata segments found")
        deduped = _dedupe(findings)
        return bytes(out), deduped, warnings

    def _jpeg_app_findings(self, marker: int, segment: bytes) -> list[Finding]:
        payload = segment[4:]
        if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            return []  # values come from the EXIF parser
        if marker == 0xE1 and payload.startswith(b"http://ns.adobe.com/xap/1.0/\x00"):
            packet = payload[len(b"http://ns.adobe.com/xap/1.0/\x00") :]
            return xmp_findings(packet)
        if marker == 0xED:
            return [Finding(key="jpeg:APP13", value="IPTC / Photoshop resource block", category="other")]
        if marker == 0xEC:
            return [Finding(key="jpeg:APP12", value="APP12 picture info block", category="other")]
        return [
            Finding(
                key=f"jpeg:APP{marker - 0xE0}",
                value=f"APP{marker - 0xE0} segment ({len(segment)} bytes)",
                category="other",
            )
        ]

    # ------------------------------------------------------------------- PNG

    def _strip_png(self, data: bytes) -> tuple[bytes, list[Finding], list[str]]:
        findings: list[Finding] = []
        warnings: list[str] = []
        out = bytearray(PNG_SIGNATURE)
        offset = len(PNG_SIGNATURE)
        size = len(data)
        seen_ihdr = False
        seen_iend = False
        saw_exif = False
        while offset + 8 <= size:
            length = int.from_bytes(data[offset : offset + 4], "big")
            chunk_type = data[offset + 4 : offset + 8]
            chunk_end = offset + 12 + length
            if chunk_end > size:
                raise CorruptFileError("PNG chunk runs past end of file")
            chunk_data = data[offset + 8 : offset + 8 + length]
            if chunk_type == b"IHDR":
                seen_ihdr = True
            if chunk_type == b"IEND":
                seen_iend = True
            if chunk_type == b"eXIf":
                saw_exif = True
            if chunk_type in PNG_METADATA_CHUNKS:
                findings.extend(self._png_chunk_findings(chunk_type, chunk_data))
            else:
                out += data[offset:chunk_end]
            offset = chunk_end
            if chunk_type == b"IEND":
                break
        if not seen_ihdr:
            raise CorruptFileError("PNG is missing the IHDR chunk")
        if not seen_iend:
            raise CorruptFileError("PNG is truncated (no IEND chunk)")
        out += data[offset:]
        exif = exif_findings(read_exif(data))
        findings.extend(exif)
        if saw_exif and not exif:
            findings.append(Finding(key="png:eXIf", value="EXIF chunk (undecodable)", category="other"))
        if not findings:
            warnings.append("no removable PNG chunks found")
        return bytes(out), _dedupe(findings), warnings

    def _png_chunk_findings(self, chunk_type: bytes, chunk_data: bytes) -> list[Finding]:
        if chunk_type == b"eXIf":
            return []  # EXIF values are decoded separately
        if chunk_type == b"tIME":
            return [Finding(key="png:tIME", value="last modification time", category="timestamp")]
        if chunk_type == b"dSIG":
            return [Finding(key="png:dSIG", value="digital signature chunk", category="other")]
        keyword, _, rest = chunk_data.partition(b"\x00")
        text = b""
        if chunk_type == b"tEXt":
            text = rest
        elif chunk_type == b"zTXt" and len(rest) >= 1:
            text = rest[1:]
            try:
                text = zlib.decompress(text)
            except zlib.error:
                text = b""
        elif chunk_type == b"iTXt" and len(rest) >= 2:
            compressed = rest[0] == 1
            payload = rest[2:]
            # language tag and translated keyword are NUL separated
            _, _, payload = payload.partition(b"\x00")
            _, _, payload = payload.partition(b"\x00")
            text = payload
            if compressed:
                try:
                    text = zlib.decompress(text)
                except zlib.error:
                    text = b""
        name = keyword.decode("latin-1", "replace").strip()
        category = PNG_TEXT_CATEGORIES.get(name.lower(), "text")
        value = _stringify(text) or f"{chunk_type.decode('ascii')} chunk"
        return [Finding(key=f"png:{name or chunk_type.decode('ascii')}", value=value, category=category)]

    # ------------------------------------------------------------------ WebP

    def _strip_webp(self, data: bytes) -> tuple[bytes, list[Finding], list[str]]:
        findings: list[Finding] = []
        warnings: list[str] = []
        if len(data) < 12:
            raise CorruptFileError("truncated WebP header")
        riff_size = int.from_bytes(data[4:8], "little")
        if riff_size + 8 > len(data):
            raise CorruptFileError("WebP RIFF size runs past end of file")
        chunks: list[tuple[bytes, bytes]] = []
        offset = 12
        end = riff_size + 8
        while offset + 8 <= end:
            fourcc = data[offset : offset + 4]
            length = int.from_bytes(data[offset + 4 : offset + 8], "little")
            payload_end = offset + 8 + length
            if payload_end > end:
                raise CorruptFileError("WebP chunk runs past end of file")
            payload = data[offset + 8 : payload_end]
            chunks.append((fourcc, payload))
            offset = payload_end + (length & 1)
        if not chunks:
            raise CorruptFileError("WebP contains no chunks")

        kept: list[tuple[bytes, bytes]] = []
        exif = exif_findings(read_exif(data))
        for fourcc, payload in chunks:
            if fourcc == b"EXIF":
                findings.extend(exif)
                if not exif:
                    findings.append(
                        Finding(key="webp:EXIF", value=f"EXIF chunk ({len(payload)} bytes)", category="other")
                    )
                continue
            if fourcc == b"XMP ":
                findings.extend(xmp_findings(payload))
                continue
            kept.append((fourcc, payload))

        if not kept:
            raise CorruptFileError("WebP lost every chunk during cleaning")

        if kept[0][0] == b"VP8X":
            flags = bytearray(kept[0][1])
            if flags:
                flags[0] &= ~0x08  # EXIF flag
                flags[0] &= ~0x04  # XMP flag
            kept[0] = (b"VP8X", bytes(flags))

        body = bytearray(b"WEBP")
        for fourcc, payload in kept:
            body += fourcc
            body += struct.pack("<I", len(payload))
            body += payload
            if len(payload) & 1:
                body += b"\x00"
        result = bytearray(b"RIFF")
        result += struct.pack("<I", len(body))
        result += body
        if not findings:
            warnings.append("no removable WebP chunks found")
        return bytes(result), _dedupe(findings), warnings


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str]] = set()
    unique: list[Finding] = []
    for finding in findings:
        signature = (finding.key, finding.value)
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(finding)
    return unique
