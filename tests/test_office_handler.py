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
    cleaned, _, _ = _clean(docx_file.read_bytes(), remove_comments=False)
    assert "word/comments.xml" in _parts(cleaned)


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
