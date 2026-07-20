"""jobs/curator/actions.py — Telegram-triggered state changes on a book row."""
import logging
import re

from jobs.curator import get_db

log = logging.getLogger(__name__)


def get_book(book_id: int) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def approve_book(book_id: int) -> dict | None:
    conn = get_db()
    try:
        conn.execute("UPDATE books SET status = 'confirmed' WHERE id = ?", (book_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def reject_book(book_id: int) -> dict | None:
    """Soft-delete: status flips to 'rejected', row stays in place."""
    conn = get_db()
    try:
        conn.execute("UPDATE books SET status = 'rejected' WHERE id = ?", (book_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def apply_edit_reply(book_id: int, text: str) -> dict | None:
    """Parse a free-text Telegram reply into a book correction, then confirm.

    Accepts either a leading digit 0-5 as the new spice_rating (rest of the
    line becomes spice_notes), or plain text which is stored as spice_notes
    only, leaving spice_rating untouched.
    """
    text = text.strip()
    match = re.match(r"^([0-5])\s*[-:.,]?\s*(.*)$", text)

    conn = get_db()
    try:
        if match:
            spice_rating = int(match.group(1))
            spice_notes = match.group(2).strip()
            conn.execute(
                "UPDATE books SET spice_rating = ?, spice_notes = ?, status = 'confirmed' "
                "WHERE id = ?",
                (spice_rating, spice_notes, book_id),
            )
        else:
            conn.execute(
                "UPDATE books SET spice_notes = ?, status = 'confirmed' WHERE id = ?",
                (text, book_id),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
