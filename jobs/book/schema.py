"""jobs/book/schema.py — DB schema for the Cover Comp Idea Generator.

First tenant of jobs/book/ (research_brief.py, project_backlog #13, would join
it later). See COVER_COMP_IDEA_GENERATOR build spec for the full design.
"""
from core.database import get_connection

CREATE_SERIES = """
CREATE TABLE IF NOT EXISTS cover_series (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    house_palette   TEXT NOT NULL,
    font_library_ids TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_FONT_LIBRARY = """
CREATE TABLE IF NOT EXISTS cover_font_library (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    display_face  TEXT NOT NULL,
    body_face     TEXT NOT NULL,
    rationale_tag TEXT,
    active        INTEGER NOT NULL DEFAULT 1
);
"""

CREATE_SYMBOLS_USED = """
CREATE TABLE IF NOT EXISTS cover_symbols_used (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id           INTEGER NOT NULL REFERENCES cover_series(id),
    book_title          TEXT NOT NULL,
    symbol_description  TEXT NOT NULL,
    accepted_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_CONCEPTS = """
CREATE TABLE IF NOT EXISTS cover_concepts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id           INTEGER NOT NULL REFERENCES cover_series(id),
    book_title          TEXT NOT NULL,
    run_timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
    theme               TEXT,
    key_concepts        TEXT,
    symbol_concept      TEXT NOT NULL,
    generation_prompt   TEXT NOT NULL,
    font_pairing        TEXT NOT NULL,
    layout_note         TEXT,
    status              TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed','accepted','rejected','superseded')),
    preview_image_path  TEXT,
    accepted_at         TEXT
);
"""

CREATE_FONT_SUGGESTIONS = """
CREATE TABLE IF NOT EXISTS cover_font_suggestions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id           INTEGER NOT NULL REFERENCES cover_series(id),
    run_timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
    display_family      TEXT NOT NULL,
    body_family         TEXT NOT NULL,
    rationale           TEXT,
    preview_image_path  TEXT,
    status              TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed','approved','rejected'))
);
"""

ALL_TABLES = [CREATE_SERIES, CREATE_FONT_LIBRARY, CREATE_SYMBOLS_USED, CREATE_CONCEPTS, CREATE_FONT_SUGGESTIONS]

# Small fixed library, seeded once — serif display, restrained weight, no
# script/decorative faces, per the build spec's house-style rule.
_SEED_FONTS = [
    ("Playfair Display", "Source Sans Pro", "serif display / clean sans body"),
    ("Libre Baskerville", "Work Sans", "literary serif / modern grotesque body"),
    ("Cormorant Garamond", "Inter", "classical serif / neutral sans body"),
    ("Spectral", "IBM Plex Sans", "editorial serif / technical sans body"),
]


def create_tables(conn=None) -> None:
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        for stmt in ALL_TABLES:
            conn.execute(stmt)
        existing = conn.execute("SELECT COUNT(*) FROM cover_font_library").fetchone()[0]
        if existing == 0:
            conn.executemany(
                "INSERT INTO cover_font_library (display_face, body_face, rationale_tag) VALUES (?, ?, ?)",
                _SEED_FONTS,
            )
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


if __name__ == "__main__":
    create_tables()
    print("cover_series / cover_font_library / cover_symbols_used / cover_concepts ready.")
