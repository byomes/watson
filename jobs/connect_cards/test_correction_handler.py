"""
Regression tests for correction_handler.py's quote-stripping /
name-validation pipeline.

Covers the bug discovered 2026-08-02: Gmail's unquoted "collapsed quote
preview" card (sender name + relative timestamp + compact "to <recipients>"
line + a body snippet, none of it '>'-prefixed or after an "on ... wrote:"
marker) was being parsed as if it were real reply content, producing 5
garbage member records from Donna's 2026-07-28 correction email (bug fixed
in this same commit).

Run:
  PYTHONPATH=/home/billyomes/watson venv/bin/python -m pytest \
    jobs/connect_cards/test_correction_handler.py -v
"""
import os

from jobs.connect_cards.correction_handler import (
    _strip_quoted_text,
    _split_reply_sections,
    _valid_name,
)

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "test_data", "donna_565_reply_raw.txt"
)

EXPECTED_NAMES = [
    "Alexandria Latham", "Bill Williamson", "Casey Avello", "Heather Travers",
    "Israel Franco", "Jacob Shindledecker", "Juanita Crook", "Kaci Gravatt",
    "Kayla McCauley", "Kenny Velez", "Megan Franco", "Micah Yomes",
    "Pastor Bill Yomes", "Tom Thomas", "Tyler McCauley", "Yousif Alfalahi",
    "donna Redman", "jonathan james Garikimukkula", "Lucie Hale", "Jesse Franco",
]

GARBAGE_LINES = [
    "Watson",
    "to bill.yomes, me, kaci.gravatt",
    "Watson — Missed Attendance Report",
    "Sunday, July 19, 2026",
]


def _load_real_reply() -> str:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return f.read()


def test_real_reply_produces_exactly_20_correct_names_and_zero_garbage():
    """Donna's actual 2026-07-28 reply (uid 565) -- the exact email that
    produced the 5 bad member records -- must now parse to exactly the 20
    real names, in order, with none of the 5 garbage lines present."""
    body = _load_real_reply()
    stripped = _strip_quoted_text(body)
    corrections, inactives = _split_reply_sections(stripped)

    assert corrections == EXPECTED_NAMES, (
        f"Expected exactly {EXPECTED_NAMES!r}, got {corrections!r}"
    )
    assert inactives == []

    for garbage in GARBAGE_LINES:
        assert garbage not in corrections, f"Garbage line {garbage!r} leaked through"

    # The relative-timestamp line specifically (the one with the U+202F
    # narrow no-break space) must not survive either.
    assert not any("days ago" in c for c in corrections)


def test_traditional_quoted_block_still_stripped():
    """Baseline regression: the standard '>'-quoted block after 'On ...
    wrote:' must still be fully removed, unaffected by this fix."""
    body = _load_real_reply()
    stripped = _strip_quoted_text(body)
    assert ">" not in stripped
    assert "Alex Patseliev" not in stripped  # only appears inside the '>' quote
    assert "wrote:" not in stripped.lower()


def test_synthetic_collapsed_quote_card_different_sender_and_date():
    """Synthetic case with a different sender name, date, and correction
    names, to confirm the fix generalizes and isn't hardcoded to Donna's
    specific strings. Uses a differently-worded relative timestamp (no
    U+202F, 'PM' instead of 'AM', singular 'day') to exercise the regex's
    tolerance."""
    body = (
        "Watson\n"
        "Wed, Jul 15, 3:45 PM (1 day ago)\n"
        "to somebody@example.com\n"
        "Watson — Missed Attendance Report\n"
        "Sunday, July 12, 2026\n"
        "\n"
        "WILMINGTON CAMPUS\n"
        "\n"
        "Jane Doe\n"
        "John Smith\n"
        "\n"
        "\n"
        "On Wed, Jul 15, 2026 at 3:45 PM Watson <watson.wcky@gmail.com> wrote:\n"
        "\n"
        "> Watson — Missed Attendance Report\n"
        "> WILMINGTON CAMPUS\n"
        "> Someone Else\n"
    )
    stripped = _strip_quoted_text(body)
    corrections, inactives = _split_reply_sections(stripped)

    assert corrections == ["Jane Doe", "John Smith"]
    assert inactives == []
    assert "Someone Else" not in corrections


def test_reply_with_no_quote_preview_card_unaffected():
    """A plain reply with only the traditional '>' quote (no collapsed
    preview card at all) must parse exactly as before this fix -- confirms
    the new branch in _strip_quoted_text() doesn't fire on normal replies."""
    body = (
        "Jane Doe\n"
        "John Smith\n"
        "\n"
        "On Wed, Jul 15, 2026 at 3:45 PM Watson <watson.wcky@gmail.com> wrote:\n"
        "> Watson — Missed Attendance Report\n"
        "> WILMINGTON CAMPUS\n"
        "> Someone Else\n"
    )
    stripped = _strip_quoted_text(body)
    corrections, inactives = _split_reply_sections(stripped)
    assert corrections == ["Jane Doe", "John Smith"]


def test_valid_name_backstop_rejects_garbage_even_without_stripping():
    """Defense-in-depth: even if _strip_quoted_text() somehow missed one of
    these lines, _valid_name() must independently reject the ones that carry
    a recognizable pattern (relative timestamp, 'to ' line, or the report's
    own subject text). 'Watson' and a bare date string are NOT expected to
    be caught here -- that's why the stripping-layer fix is the real defense,
    not this backstop; documented in the diff."""
    assert not _valid_name("Mon, Jul 20, 6:00 AM (8 days ago)")
    assert not _valid_name("to bill.yomes, me, kaci.gravatt")
    assert not _valid_name("Watson — Missed Attendance Report")
    # Sanity: real names must still pass.
    assert _valid_name("Jane Doe")
    assert _valid_name("Pastor Bill Yomes")


if __name__ == "__main__":
    test_real_reply_produces_exactly_20_correct_names_and_zero_garbage()
    test_traditional_quoted_block_still_stripped()
    test_synthetic_collapsed_quote_card_different_sender_and_date()
    test_reply_with_no_quote_preview_card_unaffected()
    test_valid_name_backstop_rejects_garbage_even_without_stripping()
    print("All tests passed.")
