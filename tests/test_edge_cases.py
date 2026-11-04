"""Error paths and rarely hit format branches.

Every sample here is synthesized byte by byte -- no real document or photo is
part of this repository.
"""

from __future__ import annotations

import io
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest
from doc_meta_strip.base import CleanOptions, CorruptFileError
from doc_meta_strip.handlers import ImageHandler, OfficeHandler, PdfHandler
from PIL import Image

from conftest import make_jpeg, make_png


def _jpeg_segments(segments: list[bytes]) -> bytes:
    base = io.BytesIO()
    Image.new("RGB", (16, 16), (7, 7, 7)).save(base, "JPEG", quality=70)
    data = base.getvalue()
    return data[:2] + b"".join(segments) + data[2:]


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    )


def _insert_png_chunks(source: Path, chunks: list[bytes], target: Path) -> Path:
    data = source.read_bytes()
    out = bytearray(data[:8])
    offset = 8
    inserted = False
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if chunk_type == b"IEND" and not inserted:
            out += b"".join(chunks)
            inserted = True
        out += data[offset:end]
        offset = end
    target.write_bytes(bytes(out))
    return target


def test_jpeg_removes_xmp_iptc_comment_and_app12() -> None:
    xmp_payload = (
        b"http://ns.adobe.com/xap/1.0/\x00"
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
        b'xmlns:xmp="http://ns.adobe.com/xap/1.0/"><xmp:CreatorTool>Fake Tool</xmp:CreatorTool>'
        b"</rdf:Description></rdf:RDF></x:xmpmeta>"
    )

    def segment(marker: int, payload: bytes) -> bytes:
        return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload

    data = _jpeg_segments(
        [
            segment(0xE1, xmp_payload),
            segment(0xED, b"Photoshop 3.0\x00fake iptc"),
            segment(0xEC, b"Ducky\x00fake app12"),
            segment(0xFE, b"a fake comment"),
        ]
    )
    cleaned, findings, _ = ImageHandler().clean_bytes(data, CleanOptions())
    keys = {finding.key for finding in findings}

    assert {"jpeg:APP13", "jpeg:APP12", "jpeg:COM"} <= keys
    assert any(key.startswith("xmp:") for key in keys)
    assert b"a fake comment" not in cleaned
    assert b"fake iptc" not in cleaned
    assert b"Fake Tool" not in cleaned
    with Image.open(io.BytesIO(cleaned)) as image:
        assert image.size == (16, 16)


def test_jpeg_truncated_segment_length_raises() -> None:
    with pytest.raises(CorruptFileError):
        ImageHandler().clean_bytes(b"\xff\xd8\xff\xe1\x10\x00", CleanOptions())


def test_jpeg_restart_marker_is_preserved() -> None:
    base = _jpeg_segments([b"\xff\xd0"])
    cleaned, findings, _ = ImageHandler().clean_bytes(base, CleanOptions())
    assert cleaned == base
    assert findings == []


def test_png_time_exif_and_broken_compression(tmp_path: Path) -> None:
    source = make_png(tmp_path / "source.png")
    target = _insert_png_chunks(
        source,
        [
            _png_chunk(b"tIME", struct.pack(">HBBBBB", 2026, 1, 1, 0, 0, 0)),
            _png_chunk(b"eXIf", b"not a real tiff header"),
            _png_chunk(b"zTXt", b"Check\x00\x00not-zlib-data"),
            _png_chunk(b"iTXt", b"Prompt\x00\x01\x00en\x00\x00not-zlib-either"),
            _png_chunk(b"dSIG", b"fake signature"),
        ],
        tmp_path / "padded.png",
    )
    cleaned, findings, _ = ImageHandler().clean_bytes(target.read_bytes(), CleanOptions())
    keys = {finding.key for finding in findings}

    assert "png:tIME" in keys
    assert "png:eXIf" in keys
    assert "png:dSIG" in keys
    assert "png:Check" in keys
    assert "png:Prompt" in keys
    for chunk in (b"tIME", b"eXIf", b"dSIG", b"zTXt", b"iTXt"):
        assert chunk not in cleaned
    with Image.open(io.BytesIO(cleaned)) as image:
        assert image.size == (24, 24)


def test_png_chunk_past_end_raises(tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 999) + b"IHDR" + b"short")
    with pytest.raises(CorruptFileError):
        ImageHandler().clean_bytes(broken.read_bytes(), CleanOptions())


def test_png_without_iend_raises(tmp_path: Path) -> None:
    data = make_png(tmp_path / "ok.png").read_bytes()
    truncated = data[: data.rindex(b"IEND") - 4]
    with pytest.raises(CorruptFileError):
        ImageHandler().clean_bytes(truncated, CleanOptions())


def test_webp_truncated_header_raises() -> None:
    with pytest.raises(CorruptFileError):
        ImageHandler().clean_bytes(b"RIFF\x10\x00\x00\x00WEBP", CleanOptions())


def test_webp_chunk_past_end_raises() -> None:
    payload = b"RIFF" + struct.pack("<I", 8 + 8) + b"WEBP" + b"EXIF" + struct.pack("<I", 500)
    with pytest.raises(CorruptFileError):
        ImageHandler().clean_bytes(payload, CleanOptions())


def test_webp_without_chunks_raises() -> None:
    payload = b"RIFF" + struct.pack("<I", 4) + b"WEBP"
    with pytest.raises(CorruptFileError):
        ImageHandler().clean_bytes(payload, CleanOptions())


def test_pdf_file_attachment_annotation_removed(tmp_path: Path) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    page = writer.pages[0]
    annotation = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/FileAttachment"),
            NameObject("/Rect"): ArrayObject(
                [NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]
            ),
            NameObject("/Contents"): TextStringObject("fake attachment annotation"),
        }
    )
    page[NameObject("/Annots")] = ArrayObject([writer._add_object(annotation)])
    source = tmp_path / "annot.pdf"
    with source.open("wb") as handle:
        writer.write(handle)

    cleaned, findings, _ = PdfHandler().clean_bytes(source.read_bytes(), CleanOptions())
    reader = PdfReader(io.BytesIO(cleaned))
    assert "/Annots" not in reader.pages[0]
    assert any(finding.key == "pdf:annotation:FileAttachment" for finding in findings)


def test_pdf_javascript_name_tree_removed(tmp_path: Path) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    action = DictionaryObject(
        {
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject("app.alert('fake')"),
        }
    )
    writer.root_object[NameObject("/Names")] = DictionaryObject(
        {
            NameObject("/JavaScript"): DictionaryObject(
                {NameObject("/Names"): ArrayObject([TextStringObject("fake"), writer._add_object(action)])}
            )
        }
    )
    source = tmp_path / "script.pdf"
    with source.open("wb") as handle:
        writer.write(handle)

    cleaned, findings, _ = PdfHandler().clean_bytes(source.read_bytes(), CleanOptions())
    reader = PdfReader(io.BytesIO(cleaned))
    assert "/Names" not in reader.root_object
    assert b"app.alert" not in cleaned
    assert any(finding.key == "pdf:javascript" for finding in findings)


def test_pdf_piece_info_reported(tmp_path: Path) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.root_object[NameObject("/PieceInfo")] = DictionaryObject(
        {NameObject("/FakeApp"): DictionaryObject({})}
    )
    source = tmp_path / "piece.pdf"
    with source.open("wb") as handle:
        writer.write(handle)

    _, findings, _ = PdfHandler().clean_bytes(source.read_bytes(), CleanOptions())
    assert any(finding.key == "pdf:PieceInfo" for finding in findings)


def test_office_dangling_ole_object_wrapper_removed(tmp_path: Path) -> None:
    import zipfile

    path = tmp_path / "ole.docx"
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:o="urn:schemas-microsoft-com:office:office"><w:body><w:p><w:r><w:object>'
        '<o:OLEObject Type="Embed" ProgID="Package" r:id="rIdEmbed"/></w:object></w:r></w:p>'
        "<w:p><w:r><w:t>Surviving text</w:t></w:r></w:p></w:body></w:document>"
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdEmbed" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/oleObject" Target="embeddings/oleObject1.bin"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", rels)
        archive.writestr("word/embeddings/oleObject1.bin", b"fake payload")
        archive.writestr("[Content_Types].xml", "<Types/>")

    cleaned, findings, _ = OfficeHandler().clean_bytes(path.read_bytes(), CleanOptions())
    with zipfile.ZipFile(io.BytesIO(cleaned)) as archive:
        body = archive.read("word/document.xml")
        assert "word/embeddings/oleObject1.bin" not in archive.namelist()
    assert b"OLEObject" not in body
    assert b"Surviving text" in body
    assert any(finding.category == "embedded" for finding in findings)


def test_office_external_relationship_is_preserved(tmp_path: Path) -> None:
    import zipfile

    path = tmp_path / "link.docx"
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/hyperlink" Target="https://example.invalid/fake" TargetMode="External"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
        )
        archive.writestr("word/_rels/document.xml.rels", rels)
    cleaned, _, _ = OfficeHandler().clean_bytes(path.read_bytes(), CleanOptions())
    with zipfile.ZipFile(io.BytesIO(cleaned)) as archive:
        assert b"example.invalid" in archive.read("word/_rels/document.xml.rels")


def test_office_malformed_parts_raise_corrupt_file_error(tmp_path: Path) -> None:
    import zipfile

    path = tmp_path / "malformed.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", "<w:document not closed")
        archive.writestr("word/_rels/document.xml.rels", "<Relationships")
    with pytest.raises(CorruptFileError, match="word/document.xml is not well formed"):
        OfficeHandler().clean_bytes(path.read_bytes(), CleanOptions())

    bad_rels = tmp_path / "bad_rels.docx"
    with zipfile.ZipFile(bad_rels, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
        )
        archive.writestr("word/_rels/document.xml.rels", "<Relationships not closed")
    with pytest.raises(CorruptFileError, match="document.xml.rels is not well formed"):
        OfficeHandler().clean_bytes(bad_rels.read_bytes(), CleanOptions())

    bad_comments = tmp_path / "bad_comments.docx"
    with zipfile.ZipFile(bad_comments, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
        )
        archive.writestr("word/comments.xml", "<w:comments not closed")
    with pytest.raises(CorruptFileError, match="word/comments.xml is not well formed"):
        OfficeHandler().clean_bytes(bad_comments.read_bytes(), CleanOptions())

    bad_ct = tmp_path / "bad_ct.docx"
    with zipfile.ZipFile(bad_ct, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
        )
        archive.writestr("[Content_Types].xml", "<Types not closed")
    with pytest.raises(CorruptFileError, match=r"\[Content_Types\]\.xml is not well formed"):
        OfficeHandler().clean_bytes(bad_ct.read_bytes(), CleanOptions())

    for prop_part in ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml"):
        bad_prop = tmp_path / f"bad_{Path(prop_part).name}"
        with zipfile.ZipFile(bad_prop, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
            )
            archive.writestr(prop_part, "<broken")
        with pytest.raises(CorruptFileError, match=f"{prop_part} is not well formed"):
            OfficeHandler().clean_bytes(bad_prop.read_bytes(), CleanOptions())


def test_module_entry_point_reports_version() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "doc_meta_strip", "--version"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "doc-meta-strip 0.1.0" in result.stdout


def test_jpeg_without_metadata_warns_once(tmp_path: Path) -> None:
    source = make_jpeg(tmp_path / "plain.jpg")
    Image.open(source).save(source, "JPEG")
    _, findings, warnings = ImageHandler().clean_bytes(source.read_bytes(), CleanOptions())
    assert findings == []
    assert warnings == ["no removable metadata segments found"]
