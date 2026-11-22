"""Text extraction that keeps page boundaries, so every passage can be cited by page.

Uses pypdf for born-digital PDFs. For scanned PDFs run OCRmyPDF first
(`ocrmypdf --output-type pdfa in.pdf out.pdf`) and register the OCR'd copy as a
derived source; Docling can replace this module for complex layouts.
"""
from pathlib import Path


def extract_pages(path: Path) -> tuple[list[str], str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return [(p.extract_text() or "") for p in reader.pages], "application/pdf"
    if suffix in {".txt", ".md", ".csv", ".json", ".html", ".htm", ".xml"}:
        text = path.read_text(errors="replace")
        # form feed = page break convention for plain-text transcriptions
        return text.split("\f"), "text/plain"
    return [""], "application/octet-stream"   # images/audio: stored, text added later via OCR
