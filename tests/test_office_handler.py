"""Office handler: OpenXML parts, tracked changes, comments and thumbnails."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from doc_meta_strip.base import CleanOptions, CorruptFileError, UnsupportedFormatError
from doc_meta_strip.handlers import OfficeHandler

from conftest import FAKE_AUTHOR, FAKE_COMPANY, FAKE_PATH, make_docx, make_xlsx

NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _clean(data: bytes, **kwargs: object) -> tuple[bytes, list, list]:
    return OfficeHandler().clean_bytes(data, CleanOptions(**kwargs))  # type: ignore[arg-type]


def _parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: archive.read(info.filename) for info in archive.infolist()}


def test_docx_personal_properties_removed(docx_file: Path) -> None:
    cleaned, findings, _ = _clean(docx_file.read_bytes())
    parts = _parts(cleaned)

    core = parts["docProps/core.xml"]
    assert FAKE_AUTHOR.encode() not in core
    assert b"Fake Internal Title" not in core
    assert b"revision" not in core

    app = parts["docProps/app.xml"]
    assert FAKE_COMPANY.encode() not in app
    assert FAKE_PATH.encode() not in app
    assert b"Fake Office" not in app
    assert b"<Pages>1</Pages>" in app

    assert "docProps/custom.xml" not in parts
    assert not any(name.startswith("docProps/thumbnail") for name in parts)


def test_docx_keeps_dates_by_default(docx_file: Path) -> None:
    cleaned, _, _ = _clean(docx_file.read_bytes())
    core = _parts(cleaned)["docProps/core.xml"]
    assert b"2026-01-01T00:00:00Z" in core
    assert b"2026-02-02T00:00:00Z" in core


def test_docx_strip_dates_option(docx_file: Path) -> None:
    cleaned, findings, _ = _clean(docx_file.read_bytes(), strip_dates=True)
    parts = _parts(cleaned)
    # Everything in core.xml was removable, so the whole part is gone.
    assert "docProps/core.xml" not in parts
    assert b"2026-01-01T00:00:00Z" not in cleaned
    assert any(finding.category == "timestamp" for finding in findings)


def test_docx_tracked_changes_are_resolved(docx_file: Path) -> None:
    cleaned, findings, _ = _clean(docx_file.read_bytes())
    document = _parts(cleaned)["word/document.xml"]

    assert b"deleted secret" not in document
    assert b"inserted text" in document
    assert b"w:del " not in document
    assert b"w:ins " not in document
    assert b"rsid" not in document
    assert b"Fake Author" not in document
    assert any(finding.key == "word:tracked-deletion" for finding in findings)


def test_docx_comments_removed_and_body_intact(docx_file: Path) -> None:
    cleaned, findings, _ = _clean(docx_file.read_bytes())
    parts = _parts(cleaned)

    assert "word/comments.xml" not in parts
    assert b"commentReference" not in parts["word/document.xml"]
    assert b"internal comment text" not in cleaned
    assert any(finding.category == "comment" for finding in findings)

    document = parts["word/document.xml"]
    assert b"Public paragraph" in document
    assert b"Third paragraph" in document
    assert b"Kept text" in document


def test_docx_keep_comments_option(docx_file: Path) -> None:
    cleaned, findings, _ = _clean(docx_file.read_bytes(), remove_comments=False)
    parts = _parts(cleaned)
    assert "word/comments.xml" in parts
    assert b"commentReference" in parts["word/document.xml"]
    assert not any(finding.category == "comment" for finding in findings)


def test_docx_opens_after_cleaning(docx_file: Path, tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    cleaned, _, _ = _clean(docx_file.read_bytes())
    target = tmp_path / "cleaned.docx"
    target.write_bytes(cleaned)

    document = docx.Document(str(target))
    texts = [paragraph.text for paragraph in document.paragraphs]
    assert "Public paragraph" in texts
    assert "Kept text inserted text" in texts
    assert "Third paragraph" in texts
    assert all("deleted secret" not in text for text in texts)
    assert document.core_properties.author in (None, "")


def test_docx_content_types_pruned(docx_file: Path) -> None:
    cleaned, _, _ = _clean(docx_file.read_bytes())
    content_types = _parts(cleaned)["[Content_Types].xml"]
    assert b"/word/comments.xml" not in content_types
    assert b"thumbnail" not in content_types


def test_docx_relationships_pruned(docx_file: Path) -> None:
    cleaned, _, _ = _clean(docx_file.read_bytes())
    parts = _parts(cleaned)
    root_rels = parts["_rels/.rels"]
    assert b"thumbnail" not in root_rels
    assert b"custom-properties" not in root_rels
    document_rels = parts["word/_rels/document.xml.rels"]
    assert b"comments" not in document_rels
    assert b"styles.xml" in document_rels


def test_xlsx_comments_removed_and_values_kept(xlsx_file: Path) -> None:
    cleaned, findings, _ = _clean(xlsx_file.read_bytes())
    parts = _parts(cleaned)

    assert not any("comment" in name.lower() for name in parts)
    assert b"internal note from Fake Author" not in cleaned
    assert b"Fake Author" not in cleaned
    sheet = parts["xl/worksheets/sheet1.xml"]
    assert b"legacyDrawing" not in sheet
    assert b"visible value" in sheet
    assert any(finding.category == "comment" for finding in findings)


def test_xlsx_opens_after_cleaning(xlsx_file: Path, tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    cleaned, _, _ = _clean(xlsx_file.read_bytes())
    target = tmp_path / "cleaned.xlsx"
    target.write_bytes(cleaned)

    workbook = openpyxl.load_workbook(target)
    sheet = workbook.active
    assert sheet["A1"].value == "visible value"
    assert sheet["B2"].value == 42
    assert sheet["A1"].comment is None


def test_xlsx_core_properties_cleared(xlsx_file: Path) -> None:
    cleaned, _, _ = _clean(xlsx_file.read_bytes())
    core = _parts(cleaned)["docProps/core.xml"]
    assert FAKE_AUTHOR.encode() not in core
    assert FAKE_COMPANY.encode() not in core


def test_corrupt_zip_raises(tmp_path: Path) -> None:
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"PK\x03\x04this is not a zip")
    with pytest.raises(CorruptFileError):
        _clean(broken.read_bytes())


def test_plain_zip_is_unsupported(tmp_path: Path) -> None:
    archive = tmp_path / "plain.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("hello.txt", "hello")
    with pytest.raises(UnsupportedFormatError):
        _clean(archive.read_bytes())


def test_pptx_docprops_are_cleaned(tmp_path: Path) -> None:
    path = tmp_path / "deck.pptx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<presentation/>")
        archive.writestr(
            "docProps/core.xml",
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
            'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:creator>Fake Author</dc:creator></cp:coreProperties>",
        )
    cleaned, findings, _ = _clean(path.read_bytes())
    parts = _parts(cleaned)
    assert "docProps/core.xml" not in parts
    assert any(finding.key == "core:creator" for finding in findings)


def test_clean_docx_reports_nothing_to_do(docx_file: Path) -> None:
    cleaned, _, _ = _clean(docx_file.read_bytes())
    again, findings, warnings = _clean(cleaned)
    assert findings == []
    assert warnings == ["no removable Office metadata found"]
    assert again == cleaned


def test_scan_reports_categories(docx_file: Path) -> None:
    result = OfficeHandler().scan(docx_file)
    categories = {finding.category for finding in result.findings}
    assert {"identity", "organization", "path", "revision", "comment", "thumbnail"} <= categories


def test_comment_author_and_text_reported(xlsx_file: Path) -> None:
    _, findings, _ = _clean(xlsx_file.read_bytes())
    assert any(finding.key == "office:comment_author" for finding in findings)
    assert any(finding.key == "office:comment_text" for finding in findings)


def test_docx_comment_author_attribute_reported(docx_file: Path) -> None:
    """Word keeps the comment author in a w:author attribute, not an element."""
    _, findings, _ = _clean(docx_file.read_bytes())
    authors = [finding.value for finding in findings if finding.key == "office:comment_author"]
    assert FAKE_AUTHOR in authors


def test_minimal_docx_without_docprops(tmp_path: Path) -> None:
    path = tmp_path / "bare.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{NS_W[1:-1]}"><w:body/></w:document>')
    cleaned, findings, warnings = _clean(path.read_bytes())
    assert findings == []
    assert warnings == ["no removable Office metadata found"]
    assert "word/document.xml" in _parts(cleaned)


def test_embedded_object_removed(tmp_path: Path) -> None:
    path = tmp_path / "embedded.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{NS_W[1:-1]}"/>')
        archive.writestr("word/embeddings/oleObject1.bin", b"fake ole payload")
    cleaned, findings, _ = _clean(path.read_bytes())
    assert "word/embeddings/oleObject1.bin" not in _parts(cleaned)
    assert any(finding.category == "embedded" for finding in findings)


def test_make_docx_helper_is_valid_zip(tmp_path: Path) -> None:
    path = make_docx(tmp_path / "helper.docx")
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
    assert make_xlsx(tmp_path / "helper.xlsx").exists()


def _docx_with_formatting_change(source: Path, target: Path) -> Path:
    """Return a copy of ``source`` whose first run has a tracked property change."""
    before = "<w:r><w:t>Third paragraph</w:t></w:r>"
    after = (
        '<w:r><w:rPr><w:b/><w:rPrChange w:id="7" w:author="Fake Author" '
        'w:date="2026-01-01T00:00:00Z"><w:rPr><w:i/></w:rPr></w:rPrChange></w:rPr>'
        "<w:t>Third paragraph</w:t></w:r>"
    )
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            payload = zin.read(info)
            if info.filename == "word/document.xml":
                text = payload.decode("utf-8")
                assert before in text
                payload = text.replace(before, after, 1).encode("utf-8")
            zout.writestr(info, payload)
    return target


def test_docx_formatting_change_is_dropped_not_nested(tmp_path: Path) -> None:
    """``w:rPrChange`` holds the *previous* properties, so it must be dropped.

    Unwrapping it nested ``w:rPr`` inside ``w:rPr``, which is invalid OpenXML
    and makes Word offer to repair the document.
    """
    source = _docx_with_formatting_change(make_docx(tmp_path / "doc.docx"), tmp_path / "changed.docx")

    cleaned, findings, _ = _clean(source.read_bytes())
    document = _parts(cleaned)["word/document.xml"].decode("utf-8")
    keys = {finding.key for finding in findings}

    assert document.count("<w:rPr>") == 1
    assert "<w:rPr><w:b /></w:rPr>" in document
    assert "rPrChange" not in document
    assert FAKE_AUTHOR not in document
    assert "word:formatting-change" in keys
    assert ("word:change_author", FAKE_AUTHOR) in {(finding.key, finding.value) for finding in findings}


def test_docx_malformed_word_xml_raises_instead_of_leaking_revisions(tmp_path: Path) -> None:
    """A broken ``word/document.xml`` must fail rather than leave ``w:del`` behind."""
    path = tmp_path / "broken_doc.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{NS_W[1:-1]}"><w:body>'
            '<w:del w:author="Fake Author"><w:r><w:delText>deleted secret</w:delText></w:r></w:del>'
            "<w:p><w:r><w:t>unclosed",
        )
    with pytest.raises(CorruptFileError, match="word/document.xml is not well formed"):
        _clean(path.read_bytes())


def test_office_zip_entry_count_limit(tmp_path: Path) -> None:
    path = tmp_path / "many_entries.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{NS_W[1:-1]}"/>')
        for index in range(5):
            archive.writestr(f"word/extra_{index}.xml", "<x/>")

    handler = OfficeHandler(max_entries=3)
    with pytest.raises(CorruptFileError, match="too many entries"):
        handler.clean_bytes(path.read_bytes(), CleanOptions())


def test_office_zip_part_and_total_uncompressed_size_limits(tmp_path: Path) -> None:
    path = make_docx(tmp_path / "doc.docx")
    data = path.read_bytes()

    small_part_handler = OfficeHandler(max_part_bytes=128)
    with pytest.raises(CorruptFileError, match="exceeds maximum uncompressed size"):
        small_part_handler.clean_bytes(data, CleanOptions())

    small_total_handler = OfficeHandler(max_part_bytes=10_000, max_total_bytes=512)
    with pytest.raises(CorruptFileError, match="exceeds maximum total uncompressed size"):
        small_total_handler.clean_bytes(data, CleanOptions())


def test_office_zip_streaming_limit_and_spoofed_header_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spoofed header size or an oversized decompressed stream is rejected safely."""
    path = make_docx(tmp_path / "doc.docx")
    data = path.read_bytes()

    # 1. If a ZIP header lies with file_size = 0 while carrying non-empty deflate
    # data, Python's CRC check trips inside _read_archive and raises CorruptFileError.
    original_infolist = zipfile.ZipFile.infolist

    def spoofed_infolist(self: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        items = original_infolist(self)
        for item in items:
            item.file_size = 0
        return items

    monkeypatch.setattr(zipfile.ZipFile, "infolist", spoofed_infolist)
    with pytest.raises(CorruptFileError, match="corrupt ZIP entry"):
        OfficeHandler(max_part_bytes=128).clean_bytes(data, CleanOptions())

    # 2. If the decompressed stream itself yields more bytes than max_part_bytes,
    # the streaming loop aborts immediately.
    monkeypatch.setattr(zipfile.ZipFile, "infolist", original_infolist)
    monkeypatch.setattr(
        zipfile.ZipFile,
        "open",
        lambda self, name, mode="r", pwd=None: io.BytesIO(b"x" * 1024),
    )
    with pytest.raises(CorruptFileError, match="exceeds maximum uncompressed size"):
        OfficeHandler(max_part_bytes=900, max_total_bytes=5000).clean_bytes(data, CleanOptions())


def _docx_with_body(path: Path, body: str, extra_parts: dict[str, bytes] | None = None) -> Path:
    """Write a minimal DOCX whose ``word/document.xml`` contains ``body``."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{NS_W[1:-1]}"><w:body>{body}</w:body></w:document>',
        )
        for name, payload in (extra_parts or {}).items():
            archive.writestr(name, payload)
    return path


def test_docx_rsid_only_part_is_cleaned_without_other_findings(tmp_path: Path) -> None:
    """A part whose only cleanup is ``w:rsid*`` attributes must still be written.

    The cleaned tree used to be thrown away whenever the part produced no
    ``Finding``, so the save IDs survived in a document that scanned clean.
    """
    source = _docx_with_body(
        tmp_path / "rsid.docx",
        '<w:p w:rsidR="00AB12CD" w:rsidRDefault="00AB12CD" w:rsidP="00FF00AA">'
        "<w:r><w:t>Public paragraph</w:t></w:r></w:p>",
    )

    cleaned, findings, _ = _clean(source.read_bytes())
    document = _parts(cleaned)["word/document.xml"]

    assert b"rsid" not in document
    assert b"Public paragraph" in document
    assert any(finding.key == "word:rsid" for finding in findings)


def test_docx_insertion_only_part_is_unwrapped(tmp_path: Path) -> None:
    """``w:ins`` is unwrapped even when no other finding exists in the part."""
    source = _docx_with_body(
        tmp_path / "ins.docx",
        '<w:p><w:ins w:id="1" w:author="Fake Author" w:date="2026-01-01T00:00:00Z">'
        "<w:r><w:t>inserted text</w:t></w:r></w:ins></w:p>",
    )

    cleaned, findings, _ = _clean(source.read_bytes())
    document = _parts(cleaned)["word/document.xml"]

    assert b"w:ins" not in document
    assert FAKE_AUTHOR.encode() not in document
    assert b"inserted text" in document
    assert any(finding.key == "word:tracked-insertion" for finding in findings)


def test_docx_embedded_media_exif_is_removed(tmp_path: Path) -> None:
    """GPS/EXIF inside ``word/media`` must be cleaned, not reported as clean."""
    from conftest import make_jpeg

    jpeg = make_jpeg(tmp_path / "photo.jpg").read_bytes()
    assert b"Exif\x00\x00" in jpeg
    source = _docx_with_body(
        tmp_path / "media.docx",
        "<w:p/>",
        extra_parts={"word/media/image1.jpeg": jpeg},
    )

    cleaned, findings, warnings = _clean(source.read_bytes())
    media = _parts(cleaned)["word/media/image1.jpeg"]

    assert b"Exif\x00\x00" not in media
    assert FAKE_AUTHOR.encode() not in media
    assert b"GPS" not in media
    assert any(finding.category == "gps" for finding in findings), warnings
    assert any(finding.key == "office:media:exif:GPS" for finding in findings)

    from PIL import Image

    with Image.open(io.BytesIO(media)) as image:
        assert image.size == (48, 32)
    assert OfficeHandler().scan(source).findings


# --------------------------------------------------- pictures outside media/ and vector formats

_GIF_WITH_COMMENT = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"  # header + 2-colour table
    b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"  # loop count: kept
    b"\x21\xfe\x0cGIFSECRETAUT\x00"  # comment: removed
    b"\x21\xff\x0bXMP DataXMP\x04ABCD\x00"  # XMP application extension: removed
    b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00"  # one pixel
    b"\x3b"
)
_SVG_WITH_METADATA = (
    b'<?xml version="1.0"?><!-- made by SVGSECRETAUTHOR -->'
    b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
    b'inkscape:version="1.0" width="4" height="4"><title>SVGSECRETTITLE</title>'
    b"<metadata>SVGSECRETMETA</metadata>"
    b'<rect width="4" height="4" fill="red"/></svg>'
)


# A real 216-byte EMF produced by Windows GDI (CreateEnhMetaFileW with the description
# "FakeDrawApp" / "Fake Secret Title", one Rectangle, CloseEnhMetaFile), not hand-built.
_REAL_GDI_EMF = bytes.fromhex(
    "01000000ac000000000000000000000009000000090000000000000000000000"
    "d2000000d200000020454d4600000100d800000003000000010000001f000000"
    "6c00000000000000000a0000a005000055020000500100000000000000000000"
    "00000000081c090080200500460061006b006500440072006100770041007000"
    "70000000460061006b0065002000530065006300720065007400200054006900"
    "74006c0065000000000000002b00000018000000000000000000000009000000"
    "090000000e00000014000000000000001000000014000000"
)


def _emf_with_description() -> bytes:
    """Return the GDI-made EMF after checking its header against [MS-EMF] 2.2.9."""
    import struct

    emf = _REAL_GDI_EMF
    assert struct.unpack_from("<II", emf, 0) == (1, 172)  # EMR_HEADER, record size
    assert emf[40:44] == b" EMF"
    version, n_bytes, n_records = struct.unpack_from("<III", emf, 44)
    assert (version, n_bytes) == (0x10000, len(emf))
    n_description, off_description = struct.unpack_from("<II", emf, 0x3C)
    assert (n_description, off_description) == (31, 108)
    text = emf[off_description : off_description + 2 * n_description].decode("utf-16-le")
    assert text == "FakeDrawApp\x00Fake Secret Title\x00\x00"
    return emf


def _assert_stable(source: Path) -> bytes:
    """Clean once; cleaning the result again must find nothing in the pictures."""
    cleaned, findings, _ = _clean(source.read_bytes())
    assert findings
    _, again_findings, _ = _clean(cleaned)
    assert not [f for f in again_findings if f.key.startswith("office:media")], again_findings
    return cleaned


def test_docx_gif_is_cleaned_and_rescans_clean(tmp_path: Path) -> None:
    source = _docx_with_body(tmp_path / "gif.docx", "<w:p/>", {"word/media/icon.gif": _GIF_WITH_COMMENT})
    cleaned = _parts(_assert_stable(source))["word/media/icon.gif"]
    assert b"GIFSECRET" not in cleaned
    assert b"XMP Data" not in cleaned
    assert b"NETSCAPE2.0" in cleaned
    assert cleaned.startswith(b"GIF89a") and cleaned.endswith(b"\x3b")
    from PIL import Image

    with Image.open(io.BytesIO(cleaned)) as image:
        assert image.size == (1, 1)
    rebuilt = _docx_with_body(tmp_path / "gif2.docx", "<w:p/>", {"word/media/icon.gif": cleaned})
    assert not OfficeHandler().scan(rebuilt).findings


def test_docx_svg_is_cleaned_and_rescans_clean(tmp_path: Path) -> None:
    source = _docx_with_body(tmp_path / "svg.docx", "<w:p/>", {"word/media/chart.svg": _SVG_WITH_METADATA})
    cleaned = _parts(_assert_stable(source))["word/media/chart.svg"]
    for secret in (b"SVGSECRET", b"inkscape"):
        assert secret not in cleaned
    assert b"<rect" in cleaned
    rebuilt = _docx_with_body(tmp_path / "svg2.docx", "<w:p/>", {"word/media/chart.svg": cleaned})
    assert not OfficeHandler().scan(rebuilt).findings


def test_docx_emf_description_is_blanked_and_rescans_clean(tmp_path: Path) -> None:
    emf = _emf_with_description()
    source = _docx_with_body(tmp_path / "emf.docx", "<w:p/>", {"word/media/image1.emf": emf})
    cleaned = _parts(_assert_stable(source))["word/media/image1.emf"]
    for text in ("FakeDrawApp", "Fake Secret Title"):
        assert text.encode("utf-16-le") in emf  # the real file does carry the description
        assert text.encode("utf-16-le") not in cleaned
    assert len(cleaned) == len(emf)
    rebuilt = _docx_with_body(tmp_path / "emf2.docx", "<w:p/>", {"word/media/image1.emf": cleaned})
    assert not OfficeHandler().scan(rebuilt).findings


def test_docx_metadata_free_vector_pictures_scan_clean(tmp_path: Path) -> None:
    """Pictures without metadata must not trip ``scan --strict``."""
    plain_gif = _GIF_WITH_COMMENT.replace(b"\x21\xfe\x0cGIFSECRETAUT\x00", b"").replace(
        b"\x21\xff\x0bXMP DataXMP\x04ABCD\x00", b""
    )
    plain_svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><rect width="1" height="1"/></svg>'
    )
    wmf = b"\xd7\xcd\xc6\x9a" + bytes(18)
    source = _docx_with_body(
        tmp_path / "plain.docx",
        "<w:p/>",
        {"word/media/a.gif": plain_gif, "word/media/b.svg": plain_svg, "word/media/c.wmf": wmf},
    )
    assert not OfficeHandler().scan(source).findings


def test_docx_unparseable_picture_is_never_reported_clean(tmp_path: Path) -> None:
    tiff = b"II*\x00" + bytes(32)
    source = _docx_with_body(tmp_path / "tiff.docx", "<w:p/>", {"word/media/scan.tif": tiff})
    assert [f.key for f in OfficeHandler().scan(source).findings] == ["office:media-uninspected"]
    broken = _docx_with_body(tmp_path / "broken.docx", "<w:p/>", {"word/media/x.gif": b"GIF89a\x01"})
    assert [f.key for f in OfficeHandler().scan(broken).findings] == ["office:media-uninspected"]


@pytest.mark.parametrize(
    "location", ["word/pics/image1.jpeg", "word/Media/image1.jpeg", "word/embedded/photo.dat"]
)
def test_docx_picture_outside_media_folder_is_still_cleaned(tmp_path: Path, location: str) -> None:
    """OPC relationships may point anywhere; the folder name is not a trust boundary."""
    from conftest import make_jpeg

    jpeg = make_jpeg(tmp_path / "photo.jpg").read_bytes()
    source = _docx_with_body(tmp_path / "pics.docx", "<w:p/>", {location: jpeg})
    assert OfficeHandler().scan(source).findings
    cleaned, findings, _ = _clean(source.read_bytes())
    media = _parts(cleaned)[location]
    assert b"Exif\x00\x00" not in media
    assert b"GPS" not in media
    assert any(finding.category == "gps" for finding in findings)
    rebuilt = _docx_with_body(tmp_path / "pics2.docx", "<w:p/>", {location: media})
    assert not OfficeHandler().scan(rebuilt).findings


def test_svg_keeps_visible_foreign_object_text_and_cleans_unnamespaced_metadata(tmp_path: Path) -> None:
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject width="5" height="5">'
        b'<div xmlns="http://www.w3.org/1999/xhtml">VISIBLE TEXT</div></foreignObject></svg>'
    )
    source = _docx_with_body(tmp_path / "fo.docx", "<w:p/>", {"word/media/fo.svg": svg})
    cleaned, findings, _ = _clean(source.read_bytes())
    assert b"VISIBLE TEXT" in _parts(cleaned)["word/media/fo.svg"]
    assert not [f for f in findings if f.key.startswith("office:media")]

    bare = b"<svg><title>BARESECRET</title><desc>BARESECRET</desc><rect/></svg>"
    source = _docx_with_body(tmp_path / "bare.docx", "<w:p/>", {"word/media/bare.svg": bare})
    assert OfficeHandler().scan(source).findings
    cleaned, _, _ = _clean(source.read_bytes())
    out = _parts(cleaned)["word/media/bare.svg"]
    assert b"BARESECRET" not in out and b"<rect" in out


def test_emf_description_beyond_header_record_is_rejected_not_zeroed(tmp_path: Path) -> None:
    """A description running past the header record must not blank the next record."""
    import struct

    emf = bytearray(_emf_with_description())
    struct.pack_into("<I", emf, 0x3C, 40)  # 108 + 80 = 188 > nSize (172), still < len(file)
    source = _docx_with_body(tmp_path / "bad.docx", "<w:p/>", {"word/media/bad.emf": bytes(emf)})
    cleaned, findings, _ = _clean(source.read_bytes())
    assert _parts(cleaned)["word/media/bad.emf"] == bytes(emf)
    assert [f.key for f in findings] == ["office:media-uninspected"]


def test_svg_foreign_object_head_meta_and_mathml_annotation_are_removed(tmp_path: Path) -> None:
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject width="5" height="5">'
        b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>HEADSECRET</title>'
        b'<meta name="author" content="METASECRET"/></head><body>VISIBLE TEXT'
        b'<math xmlns="http://www.w3.org/1998/Math/MathML"><semantics><mi>x</mi>'
        b"<annotation>MATHSECRET</annotation></semantics></math></body></html></foreignObject></svg>"
    )
    source = _docx_with_body(tmp_path / "fo2.docx", "<w:p/>", {"word/media/fo.svg": svg})
    cleaned, _, _ = _clean(source.read_bytes())
    out = _parts(cleaned)["word/media/fo.svg"]
    for secret in (b"HEADSECRET", b"METASECRET", b"MATHSECRET"):
        assert secret not in out
    assert b"VISIBLE TEXT" in out
    assert b"<mi" in out or b":mi" in out


def test_svg_removal_keeps_text_after_removed_element(tmp_path: Path) -> None:
    """Text following a removed element (its ``tail``) is visible content and must stay."""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject>'
        b'<div xmlns="http://www.w3.org/1999/xhtml">Hello <meta itemprop="x"/>WORLD-VISIBLE-A</div>'
        b"</foreignObject>"
        b"<text>Hello <title>tip</title>WORLD-VISIBLE-B</text>"
        b"<text><title>first</title>WORLD-VISIBLE-C</text></svg>"
    )
    source = _docx_with_body(tmp_path / "tail.docx", "<w:p/>", {"word/media/t.svg": svg})
    cleaned, _, _ = _clean(source.read_bytes())
    out = _parts(cleaned)["word/media/t.svg"]
    for text in (b"Hello WORLD-VISIBLE-A", b"Hello WORLD-VISIBLE-B", b"WORLD-VISIBLE-C"):
        assert text in out
    assert b"tip" not in out and b"first" not in out and b"itemprop" not in out


def test_svg_head_style_is_kept_while_title_and_meta_go(tmp_path: Path) -> None:
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject>'
        b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>HEADSECRET</title>'
        b'<meta name="author" content="METASECRET"/><style>p{color:red}</style></head>'
        b"<body><p>VISIBLE</p></body></html></foreignObject></svg>"
    )
    source = _docx_with_body(tmp_path / "head.docx", "<w:p/>", {"word/media/h.svg": svg})
    cleaned, _, _ = _clean(source.read_bytes())
    out = _parts(cleaned)["word/media/h.svg"]
    assert b"p{color:red}" in out and b"VISIBLE" in out
    assert b"HEADSECRET" not in out and b"METASECRET" not in out
