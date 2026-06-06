"""jobs/documents/pdf.py — Read and create PDF files."""
import logging
import re

log = logging.getLogger(__name__)


def read_pdf(path: str) -> str:
    from pypdf import PdfReader
    try:
        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(p.strip() for p in pages if p.strip())
    except Exception as exc:
        log.error("read_pdf failed: %s", exc)
        return f"Error reading PDF: {exc}"


def create_pdf(text: str, path: str) -> bool:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    try:
        c = canvas.Canvas(path, pagesize=letter)
        width, height = letter
        margin = 72
        y = height - margin
        for line in text.splitlines():
            if y < margin:
                c.showPage()
                y = height - margin
            c.drawString(margin, y, line)
            y -= 14
        c.save()
        return True
    except Exception as exc:
        log.error("create_pdf failed: %s", exc)
        return False


def run(message: str = None) -> str:
    if not message:
        return "PDF skill ready."
    match = re.search(r'[\w/~.-]+\.pdf', message, re.IGNORECASE)
    if not match:
        return "PDF skill ready. Provide a file path to read a PDF."
    path = match.group(0).replace("~", __import__("os").path.expanduser("~"))
    text = read_pdf(path)
    if not text:
        return f"No text extracted from {path}."
    preview = text[:1000]
    suffix = f"\n\n[{len(text)} chars total]" if len(text) > 1000 else ""
    return f"PDF: {path}\n\n{preview}{suffix}"
