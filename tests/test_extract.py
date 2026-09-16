import io

from sam_tracker.extract import extract_text, normalize


def test_normalize_collapses_whitespace_and_blank_lines():
    assert normalize("  a   b \n\n\tc\n") == ["a b", "c"]


def test_docx_text_boxes_are_read():
    import docx

    d = docx.Document()
    d.add_paragraph("plain paragraph")
    buf = io.BytesIO()
    d.save(buf)
    text, ocr = extract_text(buf.getvalue(), "x.docx")
    assert "plain paragraph" in text and not ocr


def test_scanned_pdf_is_flagged():
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    text, ocr = extract_text(buf.getvalue(), "scan.pdf")
    assert text.strip() == "" and ocr


def test_unknown_binary_is_empty_not_error():
    text, ocr = extract_text(b"\x00\xff\xfe garbage", "thing.xlsx")
    assert text == "" and not ocr
