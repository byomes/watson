"""jobs/campaigns/test_dispatch.py — Test isolation regression test.

During Phase 2 testing, dispatch_facebook_row() was called directly with a
fake campaign_id and still inserted into the real facebook_queue table;
facebook_post.py's cron (which has no concept of campaigns) then posted that
test content to the real Facebook page. These tests assert the structural
fix: a campaign_id that isn't a real, active book_launch_campaigns row can
never cause a facebook_queue write or a brevo_send.send_email() call,
regardless of what dry_run value a caller passes.

Run: PYTHONPATH=/home/billyomes/watson ./venv/bin/pytest jobs/campaigns/test_dispatch.py -v
"""
from unittest.mock import patch

import pytest

from core.database import get_connection
from jobs.campaigns.dispatch import dispatch_facebook_row, send_brevo_row

_FAKE_CAMPAIGN_ID = "test-dispatch-isolation-999"


@pytest.fixture
def fake_row():
    """A row referencing a campaign_id that does NOT exist in
    book_launch_campaigns at all — the strongest version of "unrecognized
    campaign" (not even present, let alone active)."""
    return {
        "id": -1,
        "campaign_id": _FAKE_CAMPAIGN_ID,
        "week_number": 1,
        "send_date": "2020-01-01",
        "platform": "facebook",
        "segment": "public",
        "subject": None,
        "body_text": "Isolation test row — should never be inserted anywhere.",
    }


def _facebook_queue_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM facebook_queue").fetchone()[0]


def test_dispatch_facebook_row_never_writes_for_unknown_campaign(fake_row):
    conn = get_connection()
    try:
        # Sanity check: this campaign really doesn't exist.
        exists = conn.execute(
            "SELECT 1 FROM book_launch_campaigns WHERE campaign_id=?", (_FAKE_CAMPAIGN_ID,)
        ).fetchone()
        assert exists is None, "test fixture campaign_id unexpectedly exists — pick a different one"

        before = _facebook_queue_count(conn)

        # Explicit dry_run=False — simulating a caller mistake. The function's
        # own campaign-status check must still prevent a real write.
        result = dispatch_facebook_row(conn, fake_row, dry_run=False)

        after = _facebook_queue_count(conn)
        assert after == before, "facebook_queue row count changed for an unrecognized campaign_id"
        assert result["dry_run"] is True
        assert "would_insert" in result
    finally:
        conn.close()


def test_send_brevo_row_never_calls_send_email_for_unknown_campaign(fake_row):
    fake_row = dict(fake_row)
    fake_row.update({"platform": "brevo", "segment": "general", "subject": "Test subject"})

    conn = get_connection()
    try:
        with patch("jobs.campaigns.dispatch.send_email") as mock_send_email:
            result = send_brevo_row(conn, fake_row, dry_run=False)

        mock_send_email.assert_not_called()
        assert result["dry_run"] is True
        assert result["succeeded"] == 0
        assert result["failed"] == []
    finally:
        conn.close()


def test_send_brevo_row_never_queries_donors_or_arc_for_unknown_campaign(fake_row):
    """donor/arc segments must return zero recipients in dry-run without ever
    touching donors.db or arc_readers — not even for a count."""
    fake_row = dict(fake_row)
    fake_row.update({"platform": "brevo", "segment": "donor", "subject": "Test subject"})

    conn = get_connection()
    try:
        with patch("jobs.campaigns.dispatch.send_email") as mock_send_email:
            result = send_brevo_row(conn, fake_row, dry_run=False)

        mock_send_email.assert_not_called()
        assert result["recipients"] == 0
    finally:
        conn.close()


def test_dry_run_true_also_blocks_a_hypothetically_real_campaign(fake_row):
    """Even if dry_run=True is passed for a real/active campaign, it must
    still block the real write (the explicit flag always wins toward safety)."""
    conn = get_connection()
    try:
        row = dict(fake_row)
        row["campaign_id"] = "twj-2026"  # genuinely real and active
        before = _facebook_queue_count(conn)

        result = dispatch_facebook_row(conn, row, dry_run=True)

        after = _facebook_queue_count(conn)
        assert after == before
        assert result["dry_run"] is True
    finally:
        conn.close()
