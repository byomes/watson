"""jobs/data/table_extractor.py — Extract tables from PDF files using tabula-py."""
import logging
import re

log = logging.getLogger(__name__)

_PATH_RE = re.compile(r'[\w/~.\-]+\.pdf', re.IGNORECASE)


def extract_tables(pdf_path: str) -> str:
    import tabula
    try:
        tables = tabula.read_pdf(pdf_path, pages="all", multiple_tables=True, silent=True)
    except Exception as exc:
        log.error("tabula failed: %s", exc)
        return f"Error extracting tables: {exc}"
    if not tables:
        return "No tables found in PDF."
    lines = []
    for i, df in enumerate(tables, 1):
        lines.append(f"Table {i} ({df.shape[0]} rows × {df.shape[1]} cols):")
        lines.append(df.to_string(index=False))
        lines.append("")
    return "\n".join(lines).strip()


def run(message: str = None) -> str:
    if not message:
        return "Table extractor ready. Provide a PDF file path."
    match = _PATH_RE.search(message)
    if not match:
        return "No PDF path found in message."
    import os
    path = match.group(0).replace("~", os.path.expanduser("~"))
    return extract_tables(path)
