"""Image handler: lossless stripping of EXIF, GPS, XMP and PNG text chunks."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from doc_meta_strip.base import CleanOptions, CorruptFileError, UnsupportedFormatError
from doc_meta_strip.engine import handler_for
from doc_meta_strip.handlers import ImageHandler
from PIL import Image

from conftest import FAKE_AUTHOR, FAKE_GPS, make_jpeg, make_webp


def _clean(data: bytes, **kwargs: object) -> tuple[bytes, list, list]:
    handler = ImageHandler()
    return handler.clean_bytes(data, CleanOptions(**kwargs))  # type: ignore[arg-type]


def _scan_segment(data: bytes) -> bytes:
    """Return everything from the first SOS marker to the end of the file."""
    index = data.find(b"\xff\xda")
    assert index > 0
    return data[index:]


def test_jpeg_gps_and_identity_removed(jpeg_file: Path) -> None:
    data = jpeg_file.read_bytes()
    cleaned, findings, _ = _clean(data)

    categories = {finding.category for finding in findings}
    assert {"gps", "identity", "device", "software"} <= categories
    gps_values = [f.value for f in findings if f.category == "gps"]
    assert gps_values == [FAKE_GPS]

    with Image.open(io.BytesIO(cleaned)) as image:
        exif = image.getexif()
        assert len(exif) == 0
        assert not exif.get_ifd(0x8825)
    assert b"Fake Author" not in cleaned
    assert b"Exif\x00\x00" not in cleaned


def test_jpeg_keeps_pixels_bit_identical(jpeg_file: Path) -> None:
    data = jpeg_file.read_bytes()
    cleaned, _, _ = _clean(data)

    assert _scan_segment(cleaned) == _scan_segment(data)
    with Image.open(jpeg_file) as before, Image.open(io.BytesIO(cleaned)) as after:
        assert before.size == after.size
        assert before.tobytes() == after.tobytes()


def test_jpeg_keeps_icc_profile(tmp_path: Path) -> None:
    profile = b"\x00" * 128  # obviously fake ICC payload
    source = tmp_path / "icc.jpg"
    Image.new("RGB", (16, 16), (3, 4, 5)).save(source, "JPEG", icc_profile=profile)

    cleaned, findings, warnings = _clean(source.read_bytes())
    assert findings == []
    assert warnings == ["no removable metadata segments found"]
    assert cleaned == source.read_bytes()


def test_jpeg_rejects_truncated_stream(tmp_path: Path) -> None:
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8\xff\xe1\x00\x10short")
    with pytest.raises(CorruptFileError):
        _clean(broken.read_bytes())


def test_jpeg_rejects_file_without_scan_data(tmp_path: Path) -> None:
    broken = tmp_path / "empty.jpg"
    broken.write_bytes(b"\xff\xd8\xff\xd9")
    with pytest.raises(CorruptFileError):
        _clean(broken.read_bytes())


def test_png_text_chunks_removed(png_file: Path) -> None:
    data = png_file.read_bytes()
    cleaned, findings, _ = _clean(data)

    keywords = {finding.key for finding in findings}
    assert {"png:Author", "png:Software", "png:Comment", "png:prompt"} <= keywords
    for chunk in (b"tEXt", b"zTXt", b"iTXt"):
        assert chunk not in cleaned
    assert FAKE_AUTHOR.encode() not in cleaned
    assert b"a fake prompt" not in cleaned


def test_png_pixels_bit_identical(png_file: Path) -> None:
    data = png_file.read_bytes()
    cleaned, _, _ = _clean(data)
    with Image.open(png_file) as before, Image.open(io.BytesIO(cleaned)) as after:
        assert before.tobytes() == after.tobytes()
        assert after.format == "PNG"


def test_png_truncated_raises(tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR")
    with pytest.raises(CorruptFileError):
        _clean(broken.read_bytes())


def test_webp_exif_and_xmp_removed(webp_file: Path) -> None:
    data = webp_file.read_bytes()
    cleaned, findings, _ = _clean(data)

    keys = {finding.key for finding in findings}
    assert "exif:GPS" in keys
    assert any(key.startswith("xmp:") for key in keys)
    assert b"EXIF" not in cleaned
    assert b"XMP " not in cleaned
    assert FAKE_AUTHOR.encode() not in cleaned

    # The VP8X EXIF/XMP feature flags must be cleared as well.
    assert cleaned[12:16] == b"VP8X"
    assert cleaned[20] & 0x0C == 0

    with Image.open(io.BytesIO(cleaned)) as image:
        assert image.size == (20, 20)
        assert len(image.getexif()) == 0


def test_webp_pixels_bit_identical(webp_file: Path) -> None:
    data = webp_file.read_bytes()
    cleaned, _, _ = _clean(data)
    with Image.open(webp_file) as before, Image.open(io.BytesIO(cleaned)) as after:
        assert before.tobytes() == after.tobytes()


def test_webp_without_metadata_is_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "plain.webp"
    Image.new("RGB", (10, 10), (1, 1, 1)).save(source, "WEBP", lossless=True)
    cleaned, findings, warnings = _clean(source.read_bytes())
    assert findings == []
    assert warnings == ["no removable WebP chunks found"]
    assert Image.open(io.BytesIO(cleaned)).size == (10, 10)


def test_unsupported_payload_raises() -> None:
    with pytest.raises(CorruptFileError):
        _clean(b"not an image at all")


def test_handler_detection_by_magic_bytes() -> None:
    handler = handler_for(Path("whatever.bin"), make_png_bytes())
    assert handler.name == "image"


def make_png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (0, 0, 0)).save(buffer, "PNG")
    return buffer.getvalue()


def test_scan_reports_findings(tmp_path: Path) -> None:
    path = make_jpeg(tmp_path / "scan.jpg")
    result = ImageHandler().scan(path)
    assert result.dirty is True
    assert result.ok is True
    assert result.file_format == "image"
    assert any(finding.category == "gps" for finding in result.findings)


def test_gps_conversion_handles_missing_values() -> None:
    from doc_meta_strip.handlers.image_handler import gps_summary

    assert gps_summary({}) is None
    assert gps_summary({2: (1.0, 2.0, 3.0)}) is None
    assert gps_summary({2: "nonsense", 4: (1.0, 0.0, 0.0)}) is None


def test_xmp_unparsable_packet_is_reported() -> None:
    from doc_meta_strip.handlers.image_handler import xmp_findings

    findings = xmp_findings(b"<not-xml")
    assert findings[0].key == "xmp"
    assert findings[0].value == "unparsable XMP packet"


def test_make_webp_helper_roundtrip(tmp_path: Path) -> None:
    path = make_webp(tmp_path / "x.webp", with_xmp=False)
    with Image.open(path) as image:
        assert image.size == (20, 20)


def test_unsupported_extension_is_rejected() -> None:
    with pytest.raises(UnsupportedFormatError):
        handler_for(Path("notes.txt"), b"hello")


def _plain_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (3, 4, 5)).save(buffer, "JPEG", quality=70)
    return buffer.getvalue()


def _with_app(data: bytes, marker: int, payload: bytes) -> bytes:
    """Insert an APPn segment right after SOI."""
    segment = bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload
    return data[:2] + segment + data[2:]


def test_jpeg_jfxx_thumbnail_and_mpf_are_removed() -> None:
    """APP0 is only kept for JFIF, APP2 only for an ICC profile.

    A ``JFXX`` APP0 carries a thumbnail and an ``MPF`` APP2 indexes extra
    images; both used to survive cleaning and were not even reported.
    """
    data = _with_app(_plain_jpeg(), 0xE0, b"JFXX\x00\x10FAKE-THUMBNAIL-BYTES")
    data = _with_app(data, 0xE2, b"MPF\x00FAKE-SECOND-IMAGE-INDEX")

    cleaned, findings, _ = _clean(data)
    keys = {finding.key for finding in findings}

    assert "jpeg:JFXX" in keys
    assert "jpeg:MPF" in keys
    assert any(finding.category == "thumbnail" for finding in findings)
    assert b"FAKE-THUMBNAIL-BYTES" not in cleaned
    assert b"FAKE-SECOND-IMAGE-INDEX" not in cleaned
    with Image.open(io.BytesIO(cleaned)) as image:
        assert image.size == (16, 16)


def test_jpeg_jfif_and_icc_profile_are_preserved() -> None:
    jfif = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    icc = b"ICC_PROFILE\x00\x01\x01fake-profile"
    data = _with_app(_plain_jpeg(), 0xE0, jfif)
    data = _with_app(data, 0xE2, icc)

    cleaned, findings, _ = _clean(data)

    assert b"JFIF\x00" in cleaned
    assert b"ICC_PROFILE\x00" in cleaned
    assert findings == []


def test_jpeg_progressive_multi_scan_keeps_pixels_bit_identical() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buffer, "JPEG", progressive=True)
    data = buffer.getvalue()
    assert data.count(b"\xff\xda") > 1

    cleaned, _, _ = _clean(data)
    with Image.open(io.BytesIO(data)) as before, Image.open(io.BytesIO(cleaned)) as after:
        assert before.tobytes() == after.tobytes()


def test_jpeg_strips_metadata_markers_between_scans() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buffer, "JPEG", progressive=True)
    data = buffer.getvalue()
    first_sos = data.find(b"\xff\xda")
    sos_len = int.from_bytes(data[first_sos + 2 : first_sos + 4], "big")
    cursor = first_sos + 2 + sos_len
    while cursor < len(data):
        if data[cursor] == 0xFF and data[cursor + 1] not in (
            0x00,
            0xD0,
            0xD1,
            0xD2,
            0xD3,
            0xD4,
            0xD5,
            0xD6,
            0xD7,
        ):
            break
        cursor += 1
    payload = b"secret-author-note"
    comment = b"\xff\xfe" + (len(payload) + 2).to_bytes(2, "big") + payload
    injected = data[:cursor] + comment + data[cursor:]

    cleaned, findings, _ = _clean(injected)
    keys = {finding.key for finding in findings}
    assert "jpeg:COM" in keys
    assert b"secret-author-note" not in cleaned
    with (
        Image.open(io.BytesIO(injected)) as before,
        Image.open(io.BytesIO(cleaned)) as after,
    ):
        assert before.tobytes() == after.tobytes()


def test_jpeg_trailing_data_stripped_and_reported() -> None:
    data = _plain_jpeg() + b"SECRET_TRAILING_JUNK"
    cleaned, findings, _ = _clean(data)
    keys = {finding.key for finding in findings}
    assert "jpeg:trailing_data" in keys
    assert b"SECRET_TRAILING_JUNK" not in cleaned
    with Image.open(io.BytesIO(cleaned)) as after:
        assert after.size == (16, 16)


def test_jpeg_truncated_scan_raises_corrupt() -> None:
    data = _plain_jpeg()
    first_sos = data.find(b"\xff\xda")
    truncated = data[: first_sos + 15]
    with pytest.raises(CorruptFileError):
        _clean(truncated)


def test_png_trailing_data_stripped_and_reported(png_file: Path) -> None:
    data = png_file.read_bytes() + b"SECRET_OVERLAY_BYTES"
    cleaned, findings, _ = _clean(data)
    keys = {finding.key for finding in findings}
    assert "png:trailing_data" in keys
    assert b"SECRET_OVERLAY_BYTES" not in cleaned
    with Image.open(io.BytesIO(cleaned)) as after:
        assert after.format == "PNG"


def test_webp_trailing_data_reported(webp_file: Path) -> None:
    data = webp_file.read_bytes() + b"SECRET_WEBP_TRAILING"
    cleaned, findings, _ = _clean(data)
    keys = {finding.key for finding in findings}
    assert "webp:trailing_data" in keys
    assert b"SECRET_WEBP_TRAILING" not in cleaned
    with Image.open(io.BytesIO(cleaned)) as after:
        assert after.format == "WEBP"
