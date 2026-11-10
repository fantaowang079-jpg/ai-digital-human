# tests/test_pdf_extract.py
# PDF 转文字接口回归测试：正常提取、非 PDF/空文件/损坏文件/无文字 PDF。

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.knowledge import extract_pdf_text


def make_pdf(text: str | None = "Hello PDF 12345") -> bytes:
    """构造一个最小可解析的 PDF；text=None 时生成没有正文的空白页。"""
    if text is not None:
        content = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode("latin-1")
        page = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        )
    else:
        content = b""
        page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] >>"

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        page,
    ]
    if text is not None:
        objects.append(
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"

    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


@pytest.fixture()
def client():
    return TestClient(main.app)


def test_extract_pdf_text_returns_text_and_pages():
    text, pages = extract_pdf_text(make_pdf("Hello PDF 12345"))

    assert "Hello PDF 12345" in text
    assert pages == 1


def test_pdf_extract_endpoint(client):
    resp = client.post(
        "/api/pdf/extract",
        files={"file": ("demo.pdf", make_pdf("Hello PDF 12345"), "application/pdf")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "demo.pdf"
    assert data["pages"] == 1
    assert "Hello PDF 12345" in data["text"]
    assert data["chars"] == len(data["text"])


def test_pdf_extract_rejects_non_pdf(client):
    resp = client.post(
        "/api/pdf/extract",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )

    assert resp.status_code == 400


def test_pdf_extract_rejects_empty_file(client):
    resp = client.post(
        "/api/pdf/extract",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )

    assert resp.status_code == 400


def test_pdf_extract_reports_scanned_pdf(client):
    resp = client.post(
        "/api/pdf/extract",
        files={"file": ("blank.pdf", make_pdf(None), "application/pdf")},
    )

    assert resp.status_code == 400
    assert "OCR" in resp.json()["detail"]


def test_pdf_extract_rejects_corrupt_file(client):
    resp = client.post(
        "/api/pdf/extract",
        files={"file": ("broken.pdf", b"not a pdf", "application/pdf")},
    )

    assert resp.status_code == 400
