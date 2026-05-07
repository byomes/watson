import logging
import re
from pathlib import Path

from core.database import get_connection

log = logging.getLogger(__name__)


def _insert_thought(conn, content_type, title, body, bible_passage=None):
    conn.execute(
        """
        INSERT INTO thought_library (content_type, title, body, bible_passage)
        VALUES (?, ?, ?, ?)
        """,
        (content_type, title, body, bible_passage),
    )


def _title_from_filename(path):
    name = Path(path).stem
    # Strip leading date prefix: YYYY-MM-DD or YYYYMMDD
    name = re.sub(r"^\d{4}-?\d{2}-?\d{2}[-_\s]*", "", name)
    return name.replace("-", " ").replace("_", " ").strip().title() or Path(path).stem


# ── Sermon transcript ──────────────────────────────────────────────────────

def ingest_sermon(file_path):
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Transcript not found: {path}")

    title = _title_from_filename(path)
    body = path.read_text(encoding="utf-8", errors="replace").strip()

    with get_connection() as conn:
        _insert_thought(conn, "transcript", title, body)

    log.info("[transcript]  %s", title)
    return title


# ── Voice notes ────────────────────────────────────────────────────────────

def ingest_voice_notes():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, transcript FROM voice_notes WHERE status = 'new'"
        ).fetchall()

    if not rows:
        log.info("No new voice notes to ingest")
        return 0

    count = 0
    for row in rows:
        note_id = row["id"]
        transcript = (row["transcript"] or "").strip()
        # Use first sentence (up to 80 chars) as the title
        first_sentence = re.split(r"[.!?]", transcript)[0].strip()
        title = (first_sentence[:80] + "…") if len(first_sentence) > 80 else first_sentence
        if not title:
            title = f"Voice Note #{note_id}"

        with get_connection() as conn:
            _insert_thought(conn, "voice_note", title, transcript)
            conn.execute(
                "UPDATE voice_notes SET status = 'reviewed' WHERE id = ?",
                (note_id,),
            )

        log.info("[voice_note]  %s", title)
        count += 1

    log.info("Ingested %d voice note(s)", count)
    return count


# ── Excel Bible study notes ────────────────────────────────────────────────

def ingest_bible_study(file_path):
    import openpyxl

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {path}")

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        log.warning("Excel file is empty: %s", path)
        return 0

    # Skip header row if first cell looks like a column label
    header = rows[0]
    data_rows = rows[1:] if str(header[0]).strip().lower() in ("book", "book/chapter") else rows

    count = 0
    with get_connection() as conn:
        for row in data_rows:
            if len(row) < 4:
                continue
            book, chapter, verse, notes = row[0], row[1], row[2], row[3]
            if not notes:
                continue

            passage = f"{book} {chapter}:{verse}".strip()
            title = passage
            body = str(notes).strip()

            _insert_thought(conn, "bible_study", title, body, bible_passage=passage)
            log.info("[bible_study]  %s", title)
            count += 1

    wb.close()
    log.info("Ingested %d Bible study row(s) from %s", count, path.name)
    return count


# ── Batch transcript ingest ────────────────────────────────────────────────

def ingest_sermon_folder(folder_path):
    folder = Path(folder_path)
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a directory: {folder}")

    txt_files = sorted(folder.glob("*.txt"))
    if not txt_files:
        log.info("No .txt files found in %s", folder)
        return 0

    count = 0
    for txt_file in txt_files:
        try:
            ingest_sermon(txt_file)
            count += 1
        except Exception as e:
            log.error("Failed to ingest %s: %s", txt_file.name, e)

    log.info("Batch ingest complete — %d transcript(s) from %s", count, folder)
    return count


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python library/ingestor.py voice")
        print("  python library/ingestor.py sermon <file.txt>")
        print("  python library/ingestor.py bible  <file.xlsx>")
        print("  python library/ingestor.py batch  <folder/>")
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "voice":
        n = ingest_voice_notes()
        print(f"Done. {n} voice note(s) ingested.")
    elif mode == "sermon" and len(sys.argv) == 3:
        ingest_sermon(sys.argv[2])
        print("Done.")
    elif mode == "bible" and len(sys.argv) == 3:
        n = ingest_bible_study(sys.argv[2])
        print(f"Done. {n} row(s) ingested.")
    elif mode == "batch" and len(sys.argv) == 3:
        n = ingest_sermon_folder(sys.argv[2])
        print(f"Done. {n} transcript(s) ingested.")
    else:
        print("Unknown command or missing argument.")
        sys.exit(1)
