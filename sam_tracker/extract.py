"""Turn attachment bytes into text. Returns (text, needs_ocr)."""
from __future__ import annotations

import io
import re

from pypdf import PdfReader


def extract_text(data: bytes, filename: str = "") -> tuple[str, bool]:
    name = filename.lower()
    if data[:5] == b"%PDF-" or name.endswith(".pdf"):
        return _pdf(data)
    if data[:2] == b"PK" and (name.endswith(".docx") or not name):
        try:
            return _docx(data), False
        except Exception:
            pass
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return "", False  # binary we can't read; hash comparison still works


def _pdf(data: bytes) -> tuple[str, bool]:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception:
        return "", True
    text = "\n".join(pages)
    # A scanned PDF has pages but almost no extractable characters.
    needs_ocr = len(reader.pages) > 0 and len(re.sub(r"\s", "", text)) < 20 * len(reader.pages)
    return text, needs_ocr


def _docx(data: bytes) -> str:
    """Read every text run in document order, grouped by paragraph.

    python-docx's Document.paragraphs skips text boxes, which government forms
    (SF-30 amendments, SF-1449) rely on heavily - so walk the XML directly.
    """
    import zipfile

    from lxml import etree  # bundled with python-docx

    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    MC = "http://schemas.microsoft.com/office/markup-compatibility/2006"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    # AlternateContent carries the same text box twice (Choice + Fallback); keep one.
    for fb in list(root.iter(f"{{{MC}}}Fallback")):
        fb.getparent().remove(fb)
    paragraphs: dict[int, list[str]] = {}
    for t in root.iter(f"{{{W}}}t"):
        owner = next((a for a in t.iterancestors() if a.tag == f"{{{W}}}p"), None)
        paragraphs.setdefault(id(owner), []).append(t.text or "")
    parts = ["".join(runs) for runs in paragraphs.values()]
    return "\n".join(p for p in parts if p.strip())


def normalize(text: str) -> list[str]:
    """Collapse whitespace and drop blank lines so diffs ignore layout noise."""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]
