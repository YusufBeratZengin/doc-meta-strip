"""Shared fixtures: every sample file is generated at test time.

Nothing in this repository ships a real photo or a real document -- all sample
metadata is obviously fake ("Fake Author", "Example Corp", `C:\\Users\\fake`).
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest

FAKE_AUTHOR = "Fake Author"
FAKE_COMPANY = "Example Corp"
FAKE_PATH = r"C:\Users\fake\Documents\internal"
FAKE_GPS = "41.008200, 28.978400"


# --------------------------------------------------------------------- images


def _exif_bytes() -> bytes:
    from PIL import Image

    exif = Image.Exif()
    exif[0x010F] = "FakeMake"
    exif[0x0110] = "FakeModel"
    exif[0x013B] = FAKE_AUTHOR
    exif[0x0131] = "FakeEditor 1.0"
    exif[0x0132] = "2026:01:01 00:00:00"
    exif[0x8825] = {
        1: "N",
        2: (41.0, 0.0, 29.52),
        3: "E",
        4: (28.0, 58.0, 42.24),
    }
    return exif.tobytes()


def make_jpeg(path: Path) -> Path:
    from PIL import Image

    image = Image.new("RGB", (48, 32), (12, 34, 56))
    for x in range(0, 48, 4):
        for y in range(0, 32, 4):
            image.putpixel((x, y), (200, 10, 10))
    image.save(path, "JPEG", quality=80, exif=_exif_bytes())
    return path


def make_png(path: Path) -> Path:
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    image = Image.new("RGB", (24, 24), (1, 2, 3))
    info = PngInfo()
    info.add_text("Author", FAKE_AUTHOR)
    info.add_text("Software", "FakeEditor 2.0")
    info.add_text("Comment", "hidden comment", zip=True)
    info.add_itxt("prompt", "a fake prompt used by a fake generator")
    image.save(path, "PNG", pnginfo=info)
    return path


def make_webp(path: Path, with_xmp: bool = True) -> Path:
    from PIL import Image

    image = Image.new("RGB", (20, 20), (9, 9, 9))
    image.save(path, "WEBP", lossless=True, exif=_exif_bytes())
    if with_xmp:
        _inject_webp_chunk(
            path,
            b"XMP ",
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator><rdf:Seq><rdf:li>Fake Author</rdf:li></rdf:Seq></dc:creator></rdf:Description></rdf:RDF></x:xmpmeta>',
        )
    return path


def _inject_webp_chunk(path: Path, fourcc: bytes, payload: bytes) -> None:
    import struct

    data = path.read_bytes()
    chunks: list[tuple[bytes, bytes]] = []
    offset = 12
    end = int.from_bytes(data[4:8], "little") + 8
    while offset + 8 <= end:
        name = data[offset : offset + 4]
        length = int.from_bytes(data[offset + 4 : offset + 8], "little")
        chunks.append((name, data[offset + 8 : offset + 8 + length]))
        offset += 8 + length + (length & 1)
    if chunks and chunks[0][0] == b"VP8X":
        flags = bytearray(chunks[0][1])
        flags[0] |= 0x04  # XMP flag
        chunks[0] = (b"VP8X", bytes(flags))
    chunks.insert(1, (fourcc, payload))
    body = bytearray(b"WEBP")
    for name, payload in chunks:
        body += name + struct.pack("<I", len(payload)) + payload
        if len(payload) & 1:
            body += b"\x00"
    out = bytearray(b"RIFF") + struct.pack("<I", len(body)) + body
    path.write_bytes(bytes(out))


# --------------------------------------------------------------------- office

DOCX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="jpeg" ContentType="image/jpeg"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
<Override PartName="/docProps/custom.xml" ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>
</Types>"""

DOCX_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties" Target="docProps/custom.xml"/>
<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail" Target="docProps/thumbnail.jpeg"/>
</Relationships>"""

DOCX_DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/>
</Relationships>"""

DOCX_DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
            mc:Ignorable="w14">
<w:body>
<w:p w:rsidR="00AB12CD" w:rsidRDefault="00AB12CD"><w:r><w:t>Public paragraph</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">Kept text </w:t></w:r><w:ins w:id="1" w:author="Fake Author" w:date="2026-01-01T00:00:00Z"><w:r><w:t>inserted text</w:t></w:r></w:ins><w:del w:id="2" w:author="Fake Author" w:date="2026-01-01T00:00:00Z"><w:r><w:delText>deleted secret</w:delText></w:r></w:del></w:p>
<w:p><w:r><w:t>Third paragraph</w:t></w:r><w:commentRangeStart w:id="0"/><w:commentReference w:id="0"/></w:p>
<w:sectPr/>
</w:body>
</w:document>"""

DOCX_COMMENTS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:comment w:id="0" w:author="Fake Author" w:initials="FA" w:date="2026-01-01T00:00:00Z">
<w:p><w:r><w:t>internal comment text</w:t></w:r></w:p>
</w:comment>
</w:comments>"""

DOCX_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>
</w:styles>"""

DOCX_SETTINGS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:rsids><w:rsidRoot w:val="00AB12CD"/><w:rsid w:val="00AB12CD"/></w:rsids>
</w:settings>"""

CORE_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>Fake Internal Title</dc:title>
<dc:creator>{FAKE_AUTHOR}</dc:creator>
<cp:lastModifiedBy>{FAKE_AUTHOR}</cp:lastModifiedBy>
<cp:revision>7</cp:revision>
<dcterms:created xsi:type="dcterms:W3CDTF">2026-01-01T00:00:00Z</dcterms:created>
<dcterms:modified xsi:type="dcterms:W3CDTF">2026-02-02T00:00:00Z</dcterms:modified>
</cp:coreProperties>"""

APP_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<Application>Fake Office 16.0</Application>
<Company>{FAKE_COMPANY}</Company>
<Manager>{FAKE_AUTHOR}</Manager>
<HyperlinkBase>{FAKE_PATH}</HyperlinkBase>
<Pages>1</Pages>
<Words>3</Words>
</Properties>"""

CUSTOM_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="2" name="ClientPath"><vt:lpwstr>{FAKE_PATH}</vt:lpwstr></property>
<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="3" name="Reviewer"><vt:lpwstr>{FAKE_AUTHOR}</vt:lpwstr></property>
</Properties>"""


def make_docx(path: Path) -> Path:
    thumbnail = _tiny_jpeg()
    parts = {
        "[Content_Types].xml": DOCX_CONTENT_TYPES,
        "_rels/.rels": DOCX_ROOT_RELS,
        "word/document.xml": DOCX_DOCUMENT,
        "word/_rels/document.xml.rels": DOCX_DOCUMENT_RELS,
        "word/styles.xml": DOCX_STYLES,
        "word/settings.xml": DOCX_SETTINGS,
        "word/comments.xml": DOCX_COMMENTS,
        "docProps/core.xml": CORE_XML,
        "docProps/app.xml": APP_XML,
        "docProps/custom.xml": CUSTOM_XML,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
        archive.writestr("docProps/thumbnail.jpeg", thumbnail)
    return path


def _tiny_jpeg() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (5, 5, 5)).save(buffer, "JPEG")
    return buffer.getvalue()


def make_xlsx(path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.comments import Comment

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet["A1"] = "visible value"
    sheet["B2"] = 42
    sheet["A1"].comment = Comment("internal note from Fake Author", FAKE_AUTHOR)
    workbook.save(path)
    _inject_docprops(path)
    return path


def _inject_docprops(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        entries = {info.filename: archive.read(info) for info in archive.infolist() if not info.is_dir()}
    entries["docProps/core.xml"] = CORE_XML
    entries["docProps/app.xml"] = APP_XML
    entries["docProps/custom.xml"] = CUSTOM_XML
    types = entries["[Content_Types].xml"].decode("utf-8")
    if "/docProps/custom.xml" not in types:
        types = types.replace(
            "</Types>",
            '<Override PartName="/docProps/custom.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/></Types>',
        )
        entries["[Content_Types].xml"] = types.encode("utf-8")
    rels = entries["_rels/.rels"].decode("utf-8")
    if "custom-properties" not in rels:
        rels = rels.replace(
            "</Relationships>",
            '<Relationship Id="rIdCustom" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/custom-properties" Target="docProps/custom.xml"/></Relationships>',
        )
        entries["_rels/.rels"] = rels.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)


# ------------------------------------------------------------------------ pdf


def make_pdf(path: Path) -> Path:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    page = writer.pages[0]
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 14 Tf 20 100 Td (Hello world) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.add_metadata(
        {
            "/Author": FAKE_AUTHOR,
            "/Title": "Fake Internal Title",
            "/Producer": "Fake Producer 9.9",
            "/Creator": "Fake Creator",
        }
    )
    xmp = DecodedStreamObject()
    xmp.set_data(
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
        b'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator><rdf:Seq><rdf:li>'
        + FAKE_AUTHOR.encode()
        + b"</rdf:li></rdf:Seq></dc:creator></rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    writer.root_object[NameObject("/Metadata")] = writer._add_object(xmp)
    writer.add_attachment("fake-attachment.txt", b"fake attachment body")

    thumbnail = DecodedStreamObject()
    thumbnail.set_data(_tiny_jpeg())
    thumbnail[NameObject("/Type")] = NameObject("/XObject")
    thumbnail[NameObject("/Subtype")] = NameObject("/Image")
    thumbnail[NameObject("/Width")] = NumberObject(8)
    thumbnail[NameObject("/Height")] = NumberObject(8)
    thumbnail[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
    thumbnail[NameObject("/BitsPerComponent")] = NumberObject(8)
    thumbnail[NameObject("/Filter")] = NameObject("/DCTDecode")
    page[NameObject("/Thumb")] = writer._add_object(thumbnail)

    buffer = io.BytesIO()
    writer.write(buffer)
    path.write_bytes(buffer.getvalue())
    return path


def make_encrypted_pdf(path: Path, password: str = "fake-password") -> Path:
    from pypdf import PdfReader, PdfWriter

    source = make_pdf(path)
    reader = PdfReader(str(source))
    writer = PdfWriter(clone_from=reader)
    writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    path.write_bytes(buffer.getvalue())
    return path


# -------------------------------------------------------------------- fixture


@pytest.fixture
def jpeg_file(tmp_path: Path) -> Path:
    return make_jpeg(tmp_path / "photo.jpg")


@pytest.fixture
def png_file(tmp_path: Path) -> Path:
    return make_png(tmp_path / "image.png")


@pytest.fixture
def webp_file(tmp_path: Path) -> Path:
    return make_webp(tmp_path / "image.webp")


@pytest.fixture
def docx_file(tmp_path: Path) -> Path:
    return make_docx(tmp_path / "sunum.docx")


@pytest.fixture
def xlsx_file(tmp_path: Path) -> Path:
    return make_xlsx(tmp_path / "tablo.xlsx")


@pytest.fixture
def pdf_file(tmp_path: Path) -> Path:
    return make_pdf(tmp_path / "rapor.pdf")


def strip_xml_declaration(text: str) -> str:
    return re.sub(r"^<\?xml[^>]*\?>\s*", "", text)
