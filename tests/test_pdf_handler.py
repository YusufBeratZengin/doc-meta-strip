"""PDF handler: Info dictionary, XMP, attachments, thumbnails and passwords."""

from __future__ import annotations

from pathlib import Path

import pytest
from doc_meta_strip.base import (
    CleanOptions,
    CorruptFileError,
    PasswordRequiredError,
    WrongPasswordError,
)
from doc_meta_strip.handlers import PdfHandler
from pypdf import PdfReader

from conftest import FAKE_AUTHOR, make_encrypted_pdf, make_pdf


def _clean(data: bytes, **kwargs: object) -> tuple[bytes, list, list]:
    return PdfHandler().clean_bytes(data, CleanOptions(**kwargs))  # type: ignore[arg-type]


def test_info_dictionary_removed(pdf_file: Path) -> None:
    cleaned, findings, _ = _clean(pdf_file.read_bytes())
    reader = PdfReader(__import__("io").BytesIO(cleaned))

    assert reader.metadata is None
    keys = {finding.key for finding in findings}
    assert "pdf:Info/Author" in keys
    assert "pdf:Info/Producer" in keys
    assert FAKE_AUTHOR.encode() not in cleaned
    assert b"Fake Producer" not in cleaned


def test_text_layer_preserved(pdf_file: Path) -> None:
    cleaned, _, _ = _clean(pdf_file.read_bytes())
    reader = PdfReader(__import__("io").BytesIO(cleaned))
    assert reader.pages[0].extract_text() == "Hello world"
    assert len(reader.pages) == 1


def test_xmp_packet_removed_without_leftovers(pdf_file: Path) -> None:
    cleaned, findings, _ = _clean(pdf_file.read_bytes())
    reader = PdfReader(__import__("io").BytesIO(cleaned))

    assert "/Metadata" not in reader.root_object
    assert b"xmpmeta" not in cleaned
    assert b"Fake Author" not in cleaned
    assert any(finding.key.startswith("xmp:") for finding in findings)


def test_attachments_removed(pdf_file: Path) -> None:
    cleaned, findings, _ = _clean(pdf_file.read_bytes())
    reader = PdfReader(__import__("io").BytesIO(cleaned))

    assert list(reader.attachments) == []
    assert b"fake attachment body" not in cleaned
    assert any(finding.category == "embedded" for finding in findings)


def test_thumbnails_removed(pdf_file: Path) -> None:
    cleaned, findings, _ = _clean(pdf_file.read_bytes())
    reader = PdfReader(__import__("io").BytesIO(cleaned))

    assert "/Thumb" not in reader.pages[0]
    assert any(finding.category == "thumbnail" for finding in findings)


def test_keep_embedded_option(pdf_file: Path) -> None:
    cleaned, _, _ = _clean(pdf_file.read_bytes(), remove_embedded=False)
    reader = PdfReader(__import__("io").BytesIO(cleaned))
    assert list(reader.attachments) == ["fake-attachment.txt"]


def test_keep_thumbnail_option(pdf_file: Path) -> None:
    cleaned, _, _ = _clean(pdf_file.read_bytes(), remove_thumbnails=False)
    reader = PdfReader(__import__("io").BytesIO(cleaned))
    assert "/Thumb" in reader.pages[0]


def test_dates_are_kept_unless_requested(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    source = tmp_path / "dated.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_metadata({"/CreationDate": "D:20260101000000Z", "/Author": FAKE_AUTHOR})
    with source.open("wb") as handle:
        writer.write(handle)

    kept, _, _ = _clean(source.read_bytes())
    assert PdfReader(__import__("io").BytesIO(kept)).metadata["/CreationDate"].startswith("D:2026")

    stripped, findings, _ = _clean(source.read_bytes(), strip_dates=True)
    assert PdfReader(__import__("io").BytesIO(stripped)).metadata is None
    assert any(finding.category == "timestamp" for finding in findings)


def test_second_clean_is_a_noop(pdf_file: Path) -> None:
    once, _, _ = _clean(pdf_file.read_bytes())
    twice, findings, warnings = _clean(once)
    assert findings == []
    assert warnings == []
    assert twice == once


def test_encrypted_pdf_requires_password(tmp_path: Path) -> None:
    path = make_encrypted_pdf(tmp_path / "locked.pdf")
    with pytest.raises(PasswordRequiredError):
        _clean(path.read_bytes())


def test_encrypted_pdf_rejects_wrong_password(tmp_path: Path) -> None:
    path = make_encrypted_pdf(tmp_path / "locked.pdf", password="fake-password")
    with pytest.raises(WrongPasswordError):
        _clean(path.read_bytes(), password="wrong-password")


def test_encrypted_pdf_cleaned_with_password(tmp_path: Path) -> None:
    path = make_encrypted_pdf(tmp_path / "locked.pdf", password="fake-password")
    cleaned, findings, warnings = _clean(path.read_bytes(), password="fake-password")

    reader = PdfReader(__import__("io").BytesIO(cleaned))
    assert reader.is_encrypted is False
    assert reader.pages[0].extract_text() == "Hello world"
    assert reader.metadata is None
    assert FAKE_AUTHOR.encode() not in cleaned
    assert findings
    assert any("not password protected" in warning for warning in warnings)


def test_encrypted_pdf_accepts_empty_user_password() -> None:
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Author": "Fake Author"})
    writer.encrypt(user_password="", owner_password="owner-only-secret")
    source = io.BytesIO()
    writer.write(source)

    cleaned, findings, _ = _clean(source.getvalue(), password="")

    assert findings
    assert not PdfReader(io.BytesIO(cleaned)).is_encrypted


def test_broken_pdf_raises(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nthis is not really a pdf\n")
    with pytest.raises(CorruptFileError):
        _clean(broken.read_bytes())


def test_scan_reports_pdf_metadata(pdf_file: Path) -> None:
    result = PdfHandler().scan(pdf_file)
    assert result.dirty
    assert result.file_format == "pdf"
    categories = {finding.category for finding in result.findings}
    assert {"identity", "software", "embedded", "thumbnail"} <= categories


def test_sniffing_requires_pdf_header() -> None:
    handler = PdfHandler()
    assert handler.sniff(b"%PDF-1.4 ...")
    assert not handler.sniff(b"PK\x03\x04")


def test_fixture_helper_is_readable(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "helper.pdf")
    assert PdfReader(str(path)).pages[0].extract_text() == "Hello world"


def test_damaged_page_tree_during_thumbnail_inspection_raises(
    pdf_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_pages(_self: PdfReader) -> list[object]:
        raise RuntimeError("corrupt /Pages tree")

    monkeypatch.setattr(PdfReader, "pages", property(broken_pages))
    with pytest.raises(CorruptFileError, match="damaged PDF page tree while inspecting thumbnails"):
        _clean(pdf_file.read_bytes())
    with pytest.raises(CorruptFileError, match="damaged PDF page tree while inspecting annotations"):
        _clean(pdf_file.read_bytes(), remove_thumbnails=False)


def test_damaged_info_dictionary_raises(pdf_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_metadata(_self: PdfReader) -> object:
        raise RuntimeError("corrupt /Info object")

    monkeypatch.setattr(PdfReader, "metadata", property(broken_metadata))
    with pytest.raises(CorruptFileError, match="damaged PDF Info dictionary"):
        _clean(pdf_file.read_bytes())


def test_damaged_attachment_tree_raises(pdf_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_attachments(_self: PdfReader) -> object:
        raise RuntimeError("corrupt /EmbeddedFiles name tree")

    monkeypatch.setattr(PdfReader, "attachments", property(broken_attachments))
    with pytest.raises(CorruptFileError, match="damaged PDF attachment tree"):
        _clean(pdf_file.read_bytes())


def test_damaged_xmp_stream_warns_and_strips(pdf_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf.generic import StreamObject

    def broken_get_data(_self: StreamObject) -> bytes:
        raise RuntimeError("corrupt XMP stream")

    monkeypatch.setattr(StreamObject, "get_data", broken_get_data)
    _, findings, warnings = _clean(pdf_file.read_bytes(), remove_thumbnails=False, remove_embedded=False)
    assert any(finding.key == "pdf:XMP" for finding in findings)
    assert any("damaged PDF XMP stream removed without parsing" in w for w in warnings)


def test_damaged_trailer_root_raises(pdf_file: Path) -> None:
    corrupted = pdf_file.read_bytes().replace(b"/Root", b"/Noop")
    with pytest.raises(CorruptFileError, match="damaged PDF trailer /Root"):
        _clean(corrupted)


def test_damaged_names_dictionary_raises(pdf_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf.generic import IndirectObject

    original_get_object = IndirectObject.get_object

    def broken_get_object(self: IndirectObject) -> object:
        if self.idnum == 99:
            raise RuntimeError("corrupt /Names indirect object")
        return original_get_object(self)

    monkeypatch.setattr(PdfReader, "attachments", property(lambda _self: {}))
    monkeypatch.setattr(
        PdfHandler,
        "_root",
        staticmethod(lambda _reader: {"/Names": IndirectObject(99, 0, None)}),
    )
    monkeypatch.setattr(IndirectObject, "get_object", broken_get_object)
    with pytest.raises(CorruptFileError, match="damaged PDF /Names dictionary"):
        _clean(pdf_file.read_bytes(), remove_thumbnails=False)
