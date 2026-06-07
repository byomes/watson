"""jobs/writing/document_converter.py — convert documents between formats."""
import logging
import re
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]


def _mammoth_to_html(docx_path: str) -> str:
    import mammoth
    with open(docx_path, "rb") as f:
        result = mammoth.convert_to_html(f)
    return result.value


def _mammoth_to_markdown(docx_path: str) -> str:
    import mammoth
    with open(docx_path, "rb") as f:
        result = mammoth.convert_to_markdown(f)
    return result.value


def _markdown_to_html(text: str) -> str:
    import markdown as md
    return md.markdown(text, extensions=["tables", "fenced_code", "toc"])


def _pandoc_convert(input_path: str, from_fmt: str, to_fmt: str) -> str:
    try:
        r = subprocess.run(
            ["pandoc", "-f", from_fmt, "-t", to_fmt, input_path],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return f"pandoc error: {r.stderr.strip()[:200]}"
        return r.stdout
    except FileNotFoundError:
        return "pandoc not installed — install via: sudo apt-get install pandoc"
    except subprocess.TimeoutExpired:
        return "pandoc timed out"


def convert_document(input_path: str, output_format: str) -> dict:
    """Convert a document to the requested output format."""
    p = Path(input_path).expanduser()
    if not p.exists():
        return {"success": False, "content": "", "error": f"File not found: {input_path}"}

    ext = p.suffix.lower()
    fmt = output_format.lower().strip(".")

    try:
        if ext == ".docx" and fmt == "html":
            content = _mammoth_to_html(str(p))
        elif ext == ".docx" and fmt in ("md", "markdown"):
            content = _mammoth_to_markdown(str(p))
        elif ext in (".md", ".markdown") and fmt == "html":
            content = _markdown_to_html(p.read_text(encoding="utf-8"))
        elif ext in (".md", ".markdown") and fmt in ("docx", "pdf", "rst"):
            content = _pandoc_convert(str(p), "markdown", fmt)
        elif ext == ".html" and fmt in ("md", "markdown", "pdf", "docx"):
            target = "markdown" if fmt in ("md", "markdown") else fmt
            content = _pandoc_convert(str(p), "html", target)
        elif ext == ".rst" and fmt in ("html", "md", "markdown", "pdf"):
            target = "markdown" if fmt in ("md", "markdown") else fmt
            content = _pandoc_convert(str(p), "rst", target)
        else:
            content = _pandoc_convert(str(p), ext.lstrip("."), fmt)

        return {"success": True, "content": content, "error": None}
    except ImportError as exc:
        return {"success": False, "content": "", "error": f"Missing library: {exc}"}
    except Exception as exc:
        return {"success": False, "content": "", "error": str(exc)}


def run(message: str = None) -> str:
    if not message:
        return "Usage: convert <file> to <format> (formats: html, md, pdf, docx, rst)"

    m = re.search(r'(.+?)\s+to\s+(\w+)', message, re.IGNORECASE)
    if not m:
        return "Usage: convert <file> to <format>"

    input_path = m.group(1).strip()
    output_format = m.group(2).strip()

    result = convert_document(input_path, output_format)
    if not result["success"]:
        return f"Conversion failed: {result['error']}"

    preview = result["content"][:500]
    total = len(result["content"])
    return f"Converted to {output_format} ({total} chars):\n\n{preview}{'...' if total > 500 else ''}"
