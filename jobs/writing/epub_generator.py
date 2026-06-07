"""jobs/writing/epub_generator.py — generate EPUB from markdown or text content."""
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]
EPUB_DIR = REPO / "outputs" / "epub"


def _markdown_to_html(text: str) -> str:
    try:
        import markdown as md
        return md.markdown(text, extensions=["tables", "fenced_code"])
    except ImportError:
        # Basic fallback
        text = re.sub(r'^## (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
        text = re.sub(r'^# (.+)$', r'<h1>\1</h1>', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        text = re.sub(r'\n\n', '</p><p>', text)
        return f"<p>{text}</p>"


def generate_epub(title: str, author: str, content: str, output_path: str = None) -> dict:
    """Generate an EPUB file from markdown/text content."""
    try:
        from ebooklib import epub
    except ImportError:
        return {"success": False, "path": "", "error": "ebooklib not installed"}

    EPUB_DIR.mkdir(parents=True, exist_ok=True)

    book = epub.EpubBook()
    book.set_identifier(str(uuid.uuid4()))
    book.set_title(title)
    book.set_language("en")
    book.add_author(author)

    # Split content into chapters by H1 or H2 headings
    sections = re.split(r'^#{1,2} ', content, flags=re.MULTILINE)
    chapters = []

    if len(sections) <= 1:
        # Single chapter
        html_content = _markdown_to_html(content)
        chapter = epub.EpubHtml(title=title, file_name="chapter_1.xhtml", lang="en")
        chapter.content = f"<html><body><h1>{title}</h1>{html_content}</body></html>"
        book.add_item(chapter)
        chapters.append(chapter)
    else:
        # First section is preamble (before first heading)
        for i, section in enumerate(sections):
            if not section.strip():
                continue
            # Re-extract heading from first line
            first_line, _, body = section.partition("\n")
            chapter_title = first_line.strip() if i > 0 else title
            html_body = _markdown_to_html(body)
            chapter = epub.EpubHtml(title=chapter_title, file_name=f"chapter_{i+1}.xhtml", lang="en")
            chapter.content = f"<html><body><h1>{chapter_title}</h1>{html_body}</body></html>"
            book.add_item(chapter)
            chapters.append(chapter)

    # Navigation
    book.toc = chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters

    # Output path
    if not output_path:
        slug = re.sub(r'[^\w]+', '-', title.lower()).strip('-')
        date = datetime.utcnow().strftime("%Y%m%d")
        output_path = str(EPUB_DIR / f"{date}-{slug}.epub")

    epub.write_epub(output_path, book)
    return {"success": True, "path": output_path, "chapters": len(chapters), "error": None}


def generate_from_file(file_path: str, title: str = None, author: str = "Watson") -> dict:
    p = Path(file_path).expanduser()
    if not p.exists():
        return {"success": False, "path": "", "error": f"File not found: {file_path}"}

    content = p.read_text(encoding="utf-8", errors="ignore")
    if not title:
        # Try to extract title from first H1
        m = re.search(r'^# (.+)$', content, re.MULTILINE)
        title = m.group(1) if m else p.stem

    return generate_epub(title, author, content)


def run(message: str = None) -> str:
    if not message:
        return "Usage: generate epub from <file.md> [title: <title>] [author: <author>]"

    # extract file
    file_match = re.search(r'from\s+(\S+)', message, re.IGNORECASE)
    title_match = re.search(r'title:\s*(.+?)(?:\s+author:|$)', message, re.IGNORECASE)
    author_match = re.search(r'author:\s*(.+?)$', message, re.IGNORECASE)

    if not file_match:
        return "Specify a source file: generate epub from <file.md>"

    file_path = file_match.group(1).strip()
    title = title_match.group(1).strip() if title_match else None
    author = author_match.group(1).strip() if author_match else "Watson"

    result = generate_from_file(file_path, title=title, author=author)
    if not result["success"]:
        return f"EPUB generation failed: {result['error']}"
    return f"EPUB created: {result['path']} ({result['chapters']} chapter(s))"
