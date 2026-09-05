"""jobs/campaigns/dispatch.py — Shared send-dispatch logic for the book-launch
campaign system. Used by:
  - the dashboard "Approve All" button (jobs/campaigns/campaign_routes.py)
  - the Telegram "Approve All" callback (bot.py's handle_campaign_callback)
  - jobs/campaigns/brevo_dispatcher.py's periodic cron sweep

Facebook rows are handed off to the EXISTING facebook_queue table/cron
(jobs/facebook/facebook_post.py) — this module never posts to Facebook
directly and never touches facebook_post.py.

Brevo rows are sent directly via jobs/email_job/brevo_send.py, one recipient
at a time, with a small delay between sends.

TEST ISOLATION (added after a real incident — see bug notes / commit history):
during Phase 2 testing, a direct call to dispatch_facebook_row() with a fake
campaign_id still inserted into the real facebook_queue table, and
facebook_post.py's own cron (which has no concept of campaigns and just polls
that table) posted the test content to the real Facebook page. dry_run is not
just a caller-supplied flag here — dispatch_facebook_row() and
send_brevo_row() independently verify the campaign_id is a real, currently
active row in book_launch_campaigns before doing anything real, and force
dry-run behavior regardless of what the caller passed if it isn't. A test (or
any other caller) cannot opt back into a real write/send for a campaign_id
that isn't genuinely active — that gate lives in the two functions that do
the actual dangerous work, not in caller discipline.
"""
import json
import logging
import os
import re
import time

import requests
from dotenv import load_dotenv

from core.database import get_connection
from core.vacation import vacation_gate
from jobs.campaigns.brevo_contacts import list_contacts as brevo_list_contacts
from jobs.email_job.brevo_send import send_email

load_dotenv(os.path.expanduser("~/watson/.env"))

log = logging.getLogger(__name__)

_SEND_DELAY_SECONDS = 0.2
_LINK_RE = re.compile(r"(https?://\S+|(?<!\S)williamckyomes\.com\S*)")

TELEGRAM_BOT_TOKEN = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")


def _send_telegram(text: str) -> None:
    if vacation_gate("normal", "jobs.campaigns.dispatch", text):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def _slugify(title: str) -> str:
    return title.strip().lower().replace(" ", "-")


def _is_real_active_campaign(conn, campaign_id: str) -> bool:
    """The structural safety check: only a campaign_id that actually exists in
    book_launch_campaigns with status='active' is allowed to cause a real
    facebook_queue write or a real send_email() call. Everything else — a
    typo, a fake test campaign_id, a paused/archived campaign — is treated as
    dry-run regardless of what any caller passes."""
    row = conn.execute(
        "SELECT 1 FROM book_launch_campaigns WHERE campaign_id=? AND status='active'",
        (campaign_id,),
    ).fetchone()
    return row is not None


# ── Recipient resolution ─────────────────────────────────────────────────────

def resolve_recipients(conn, campaign_id: str, segment: str, dry_run: bool = False) -> list[dict]:
    """Return [{"email": ..., "name": ...}, ...] for a book_launch_sends
    segment value. 'general' is the Kit snapshot taken at import time (a
    campaign-scoped table, not raw donor/ARC data, so it's still queried for a
    real count even in dry-run); 'donor' and 'arc' are live queries against
    donors.db/arc_readers — those are never touched at all when dry_run=True,
    not even for a count."""
    if dry_run and segment in ("donor", "arc"):
        return []

    if segment == "general":
        rows = conn.execute(
            "SELECT email, name FROM book_launch_contacts WHERE campaign_id=? AND segment='general'",
            (campaign_id,),
        ).fetchall()
        return [{"email": r["email"], "name": r["name"] or ""} for r in rows]

    if segment == "donor":
        import sqlite3
        donors_conn = sqlite3.connect(os.path.expanduser("~/watson/data/donors.db"))
        donors_conn.row_factory = sqlite3.Row
        try:
            rows = donors_conn.execute(
                "SELECT email, name FROM donors WHERE segment != 'lapsed-donor' "
                "AND email IS NOT NULL AND email != ''"
            ).fetchall()
        finally:
            donors_conn.close()
        return [{"email": r["email"], "name": r["name"] or ""} for r in rows]

    if segment == "arc":
        campaign = conn.execute(
            "SELECT book_title FROM book_launch_campaigns WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        book_slug = _slugify(campaign["book_title"]) if campaign else None

        import sqlite3
        arc_conn = sqlite3.connect(os.path.expanduser("~/watson/data/watson.db"))
        arc_conn.row_factory = sqlite3.Row
        try:
            if book_slug:
                rows = arc_conn.execute(
                    "SELECT email, first_name, last_name FROM arc_readers "
                    "WHERE status='active' AND book_slug=?",
                    (book_slug,),
                ).fetchall()
            else:
                rows = arc_conn.execute(
                    "SELECT email, first_name, last_name FROM arc_readers WHERE status='active'"
                ).fetchall()
        finally:
            arc_conn.close()
        return [
            {"email": r["email"], "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip()}
            for r in rows
        ]

    raise ValueError(f"Unknown segment for recipient resolution: {segment!r}")


def resolve_custom_recipients(row, dry_run: bool = False) -> list[dict]:
    """Return [{"email", "name"}, ...] for a Comms Desk row whose
    recipient_mode isn't the default 'segment' — a specific Brevo list
    (looked up live, same as donor/arc: skipped entirely in dry_run since it's
    a real network call to Brevo) or a hand-picked set of individual contacts
    (no network call either way, just the JSON already stored on the row)."""
    mode = row.get("recipient_mode")
    detail = json.loads(row["recipient_detail"]) if row.get("recipient_detail") else {}

    if mode == "brevo_list":
        if dry_run:
            return []
        return brevo_list_contacts(list_id=detail["list_id"])

    if mode == "custom_emails":
        return [{"email": e["email"], "name": e.get("name") or ""} for e in detail.get("emails", [])]

    raise ValueError(f"Unknown recipient_mode for recipient resolution: {mode!r}")


# ── Facebook dispatch (queue-only — never posts directly) ──────────────────

def _facebook_ordinal(conn, campaign_id: str, week_number: int, send_id: int) -> int:
    """Post 1 / Post 2 label, derived from insertion order within the week —
    book_launch_sends has no explicit ordinal column."""
    rows = conn.execute(
        "SELECT id FROM book_launch_sends "
        "WHERE campaign_id=? AND week_number=? AND platform='facebook' ORDER BY id",
        (campaign_id, week_number),
    ).fetchall()
    for i, r in enumerate(rows, start=1):
        if r["id"] == send_id:
            return i
    return 1


def dispatch_facebook_row(conn, row, dry_run: bool = False) -> dict:
    """Insert a book_launch_sends Facebook row into the existing facebook_queue
    table. facebook_post.py's own cron (unchanged) picks it up and fires it —
    it already handles the no-image case (posts to /feed, not /photos).

    dry_run=True (or a campaign_id that isn't a real active campaign, checked
    unconditionally below) never executes the INSERT — it only returns what
    would have been inserted, for test assertions."""
    real_dry_run = dry_run or not _is_real_active_campaign(conn, row["campaign_id"])

    ordinal = _facebook_ordinal(conn, row["campaign_id"], row["week_number"], row["id"])
    title = f"{row['campaign_id']} Wk{row['week_number']} Post{ordinal}"
    link_m = _LINK_RE.search(row["body_text"] or "")
    url = link_m.group(0) if link_m else None
    # .get(): pre-existing book-launch campaign rows (and this function's own
    # test fixtures) predate the send_time column — NULL/missing means "any
    # time that day", same as before this column existed.
    send_time = row.get("send_time")
    scheduled_time = f"{row['send_date']} {send_time}:00" if send_time else f"{row['send_date']} 00:00:00"
    # .get() rather than row["image_path"]: callers (and the test isolation
    # fixtures) may pass a row dict from before this column existed — treat a
    # missing key the same as an explicit NULL rather than raising.
    image_path = row.get("image_path")

    would_insert = {
        "title": title, "summary": None, "url": url, "draft_text": row["body_text"],
        "status": "approved", "scheduled_time": scheduled_time, "image_path": image_path,
    }

    if real_dry_run:
        log.info("[DRY RUN] Would insert into facebook_queue: %s", would_insert)
        return {"dry_run": True, "would_insert": would_insert}

    conn.execute(
        """INSERT INTO facebook_queue
           (title, summary, url, draft_text, status, scheduled_time, image_path)
           VALUES (?, NULL, ?, ?, 'approved', ?, ?)""",
        (title, url, row["body_text"], scheduled_time, image_path),
    )
    conn.commit()
    return {"dry_run": False, "inserted": would_insert}


# ── Brevo dispatch (sends directly) ─────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    """Crude tag-stripped plain-text fallback for Brevo's required textContent
    field, when the real content is Comms Desk's MJML-rendered HTML."""
    return _TAG_RE.sub("", html or "").strip()


def send_brevo_row(conn, row, dry_run: bool = False) -> dict:
    """Send one book_launch_sends Brevo row to every recipient in its segment,
    then mark the row sent. Never raises — one recipient failure doesn't abort
    the batch, and the row itself is only marked 'sent' once the batch is done
    (success or partial failure alike; a total outage still marks it sent so a
    broken segment doesn't retry forever — see the Telegram summary for the
    real per-recipient outcome).

    dry_run=True (or a campaign_id that isn't a real active campaign, checked
    unconditionally below) never calls send_email() and never touches
    donors.db/arc_readers — it only reports what would have been sent, and
    leaves the book_launch_sends row untouched.

    Comms Desk email rows (source='comms_desk') carry one more independent
    gate: admin_approved_at must be set (via jobs/comms/api.py's admin-only
    /approve-send route) regardless of dry_run or status='approved' — a
    volunteer marking a row 'ready' is not enough on its own. This check
    lives here, not in any one caller, for the same reason
    _is_real_active_campaign() does: every path that can trigger a real send
    (the periodic brevo_dispatcher.py sweep, the dashboard/Telegram "Approve
    All" flow) goes through this function, so gating it here is the only way
    to make the rule unconditional. Temporary — meant to come out once
    Watson's Comms Desk pipeline has earned trust; book-launch campaign rows
    (source != 'comms_desk') are untouched by this and keep sending on
    'approved' alone."""
    real_dry_run = dry_run or not _is_real_active_campaign(conn, row["campaign_id"])

    if row.get("source") == "comms_desk" and not row.get("admin_approved_at"):
        log.info(
            "Blocked send_id=%s: Comms Desk email awaiting admin approval", row["id"],
        )
        return {"dry_run": False, "blocked": "pending_admin_approval", "send_id": row["id"]}

    recipient_mode = row.get("recipient_mode") or "segment"
    if recipient_mode == "segment":
        recipients = resolve_recipients(conn, row["campaign_id"], row["segment"], dry_run=real_dry_run)
    else:
        recipients = resolve_custom_recipients(row, dry_run=real_dry_run)

    if real_dry_run:
        label = row["subject"] or (row["body_text"] or "").splitlines()[0][:60]
        log.info(
            "[DRY RUN] Would send %r to %d %s contact(s) for campaign_id=%s "
            "(no email sent, no DB write)",
            label, len(recipients), row["segment"], row["campaign_id"],
        )
        return {
            "dry_run": True, "send_id": row["id"], "recipients": len(recipients),
            "succeeded": 0, "failed": [],
        }

    # Comms Desk rows carry MJML-rendered HTML in body_text (see jobs/comms/) —
    # sent as htmlContent, with a tag-stripped fallback for the required
    # textContent field. Every other book-launch row's body_text is plain
    # text written directly, unaffected by this branch.
    is_html = row.get("source") == "comms_desk"
    html_body = row["body_text"] if is_html else None
    text_body = _strip_html(row["body_text"]) if is_html else row["body_text"]

    succeeded, failed = 0, []
    for recipient in recipients:
        try:
            result = send_email(
                to_email=recipient["email"],
                to_name=recipient["name"],
                subject=row["subject"] or "",
                text_body=text_body,
                html_body=html_body,
            )
            if result["success"]:
                succeeded += 1
            else:
                failed.append((recipient["email"], result["error"]))
        except Exception as exc:
            failed.append((recipient["email"], str(exc)))
        time.sleep(_SEND_DELAY_SECONDS)

    conn.execute(
        "UPDATE book_launch_sends SET status='sent', sent_at=datetime('now') WHERE id=?",
        (row["id"],),
    )
    conn.commit()

    label = row["subject"] or (row["body_text"] or "").splitlines()[0][:60]
    if recipient_mode == "brevo_list":
        audience = json.loads(row["recipient_detail"])["list_name"]
    elif recipient_mode == "custom_emails":
        audience = "hand-picked"
    else:
        audience = row["segment"]
    summary_lines = [
        f"Sent '{label}' to {len(recipients)} {audience} contacts — "
        f"{succeeded} succeeded, {len(failed)} failed."
    ]
    for email, error in failed[:10]:
        summary_lines.append(f"  ✗ {email}: {error}")
    if len(failed) > 10:
        summary_lines.append(f"  ...and {len(failed) - 10} more failures.")
    _send_telegram("\n".join(summary_lines))

    return {"dry_run": False, "send_id": row["id"], "recipients": len(recipients), "succeeded": succeeded, "failed": failed}


# ── Approve-all entry point (shared by dashboard + Telegram) ───────────────

def approve_week(campaign_id: str, week_number: int, dry_run: bool = False) -> dict:
    """Set status='approved' on every not-yet-approved row for this
    campaign+week, dispatch Facebook rows to facebook_queue immediately, and
    for Brevo rows whose send_date has already arrived, send right now instead
    of waiting for brevo_dispatcher.py's next cron tick.

    dry_run: the REAL approval paths (campaign_routes.py's approve endpoint,
    bot.py's handle_campaign_callback) must always pass dry_run=False
    explicitly — do not change this to a bare `approve_week(campaign_id,
    week_number)` call or make the default conditional there. That said,
    dispatch_facebook_row() and send_brevo_row() each independently re-check
    that campaign_id is a real, active book_launch_campaigns row before doing
    anything real, regardless of the dry_run value passed through from here —
    so even a caller mistake here can't turn into a real write/send for a
    bad campaign_id. This default (False) only matters for a genuinely active
    campaign; it does not weaken that guarantee."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM book_launch_sends WHERE campaign_id=? AND week_number=? "
            "AND status NOT IN ('approved', 'sent')",
            (campaign_id, week_number),
        ).fetchall()

        approved_count = 0
        dispatched_facebook = 0
        dispatched_brevo = 0
        today = conn.execute("SELECT date('now')").fetchone()[0]

        for row in rows:
            if not dry_run:
                conn.execute("UPDATE book_launch_sends SET status='approved' WHERE id=?", (row["id"],))
                conn.commit()
            row = dict(row)
            row["status"] = "approved"
            approved_count += 1

            if row["platform"] == "facebook":
                dispatch_facebook_row(conn, row, dry_run=dry_run)
                dispatched_facebook += 1
            elif row["platform"] == "brevo" and row["send_date"] <= today:
                send_brevo_row(conn, row, dry_run=dry_run)
                dispatched_brevo += 1

        return {
            "campaign_id": campaign_id, "week_number": week_number, "dry_run": dry_run,
            "approved": approved_count, "facebook_queued": dispatched_facebook,
            "brevo_sent_now": dispatched_brevo,
        }
    finally:
        conn.close()
