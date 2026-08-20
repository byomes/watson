"""jobs/privacy/remove.py — submit_removal(): the one place Privacy Guard
actually contacts a broker to request removal. Only ever runs after Bill
taps Approve on a specific match in Telegram (bot.py:handle_privacy_callback).

Runs as its own subprocess, dispatched via subprocess.Popen from bot.py —
never in-process inside watson-bot.service. Same hard rule as everywhere
else Playwright touches this codebase (see jobs/browser/browser_service.py's
module docstring: browser jobs must be one-off subprocess.Popen invocations,
never called synchronously from a request-serving process). Because there's
no in-process await from bot.py, this module sends its own follow-up
Telegram result message once submission finishes.

CLI: python -m jobs.privacy.remove --removal-id N

KNOWN GAP (project_backlog id=37): _submit_form()'s
success path is "the click didn't throw", not a verified confirmation signal —
see the comment at the return statement below for what that means in practice
and why it wasn't fixed by just adding a text check.
"""
import argparse
import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from core.database import get_connection
from jobs.browser.browser_service import get_page, goto_safe, log_browser_failure
from jobs.email_job.brevo_send import send_email
from jobs.privacy import send_telegram

log = logging.getLogger(__name__)

LOG_DIR = Path.home() / "watson" / "logs" / "privacy"


def _load_removal(removal_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT r.*, p.name AS person_name,
                      b.name AS broker_name, b.opt_out_method, b.opt_out_target, b.form_selectors
               FROM privacy_removals r
               JOIN family_profiles p ON p.id = r.person_id
               JOIN privacy_brokers b ON b.id = r.broker_id
               WHERE r.id=?""",
            (removal_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _mark_submitted(removal_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE privacy_removals
               SET status='submitted', submitted_at=datetime('now'),
                   next_rescan_at=datetime('now','+7 days'), failure_reason=NULL
               WHERE id=?""",
            (removal_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_failed(removal_id: int, reason: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE privacy_removals SET status='failed', failure_reason=? WHERE id=?",
            (reason[:500], removal_id),
        )
        conn.commit()
    finally:
        conn.close()


# Hostnames reCAPTCHA/Turnstile-style challenges load resources from —
# used only by the dry-run diagnostic below to notice a challenge firing
# without needing to parse the DOM for it.
_CHALLENGE_HOST_HINTS = ("google.com/recaptcha", "recaptcha.net", "gstatic.com/recaptcha")


async def _submit_form(removal: dict, dry_run: bool = False):
    """Navigates and fills the opt-out form exactly as a real submission
    would. dry_run=True stops before the final page.click(submit_button)
    and instead returns a diagnostics dict (reCAPTCHA iframe/token
    presence, any recaptcha-related network requests, console messages,
    a screenshot) — used only to manually assess a broker's reCAPTCHA
    behavior before deciding whether to activate it. Real submissions
    (submit_removal without dry_run) never take this branch.

    Non-dry-run return: (ok: bool, error: str | None) — unchanged shape.
    Dry-run return: dict, always with "ok" and "dry_run": True.
    """
    selectors = json.loads(removal["form_selectors"] or "{}")
    submit_button = selectors.get("submit_button")
    if not removal["opt_out_target"] or not submit_button:
        msg = "broker not fully verified (missing opt_out_target/submit_button selector)"
        return {"ok": False, "dry_run": True, "error": msg} if dry_run else (False, msg)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    challenge_requests = []
    console_messages = []

    async with get_page() as page:
        if dry_run:
            page.on("request", lambda req: (
                challenge_requests.append(req.url)
                if any(h in req.url for h in _CHALLENGE_HOST_HINTS) else None
            ))
            page.on("console", lambda msg: console_messages.append(f"{msg.type}: {msg.text}"))

        ok = await goto_safe(page, removal["opt_out_target"], wait_until="networkidle")
        if not ok:
            msg = "could not load opt-out page (robots.txt disallow or navigation failure)"
            return {"ok": False, "dry_run": True, "error": msg} if dry_run else (False, msg)
        try:
            # Only the field shapes actually confirmed common across these
            # brokers' opt-out forms during Phase 2 verification are handled
            # here (name / profile-URL / confirmation-email) — a broker
            # whose form needs something else stays unverified (active=0)
            # rather than this code guessing at unknown selectors.
            if selectors.get("name_field"):
                await page.fill(selectors["name_field"], removal["person_name"])
            if selectors.get("url_field"):
                await page.fill(selectors["url_field"], removal["matched_url"] or "")
            if selectors.get("email_field"):
                await page.fill(selectors["email_field"], os.getenv("WATSON_GMAIL_ADDRESS", ""))

            if dry_run:
                # Give any async challenge JS (invisible reCAPTCHA fires on
                # a timer/interaction, not necessarily on page load) a beat
                # to run before inspecting DOM state — never clicks submit.
                await page.wait_for_timeout(1500)
                iframe = await page.query_selector("iframe[src*='recaptcha']")
                token_el = await page.query_selector("#g-recaptcha-response")
                token_value = await token_el.input_value() if token_el else None
                shot_path = LOG_DIR / f"dryrun-{removal['id']}-{datetime.now():%Y%m%d%H%M%S}.png"
                await page.screenshot(path=str(shot_path), full_page=True)
                return {
                    "ok": True,
                    "dry_run": True,
                    "recaptcha_iframe_present": iframe is not None,
                    "recaptcha_token_present": bool(token_value),
                    "recaptcha_token_length": len(token_value) if token_value else 0,
                    "challenge_network_requests": challenge_requests,
                    "console_messages": console_messages[-20:],
                    "screenshot": str(shot_path),
                }

            await page.click(submit_button)
            await page.wait_for_timeout(2000)
        except Exception as exc:
            shot_path = LOG_DIR / f"removal-{removal['id']}-{datetime.now():%Y%m%d%H%M%S}.png"
            try:
                await page.screenshot(path=str(shot_path))
            except Exception:
                pass
            log_browser_failure("privacy.remove form submit", removal["opt_out_target"], exc)
            msg = f"form submission failed (screenshot: {shot_path})"
            return {"ok": False, "dry_run": True, "error": msg} if dry_run else (False, msg)
    # KNOWN GAP, deliberately not fixed this pass (project_backlog id=37): this
    # only means the click() call didn't throw — no on-page confirmation
    # text/state/redirect is checked. For Spokeo specifically, checked live
    # 2026-08-20: the opt-out page's own copy states completion requires
    # clicking a link in a follow-up confirmation EMAIL ("To complete this
    # process, we will send you a confirmation email. Please click the link
    # in the email.") — i.e. the real flow is two steps (form submit + email
    # link click) even though it renders as one page. Nothing in this module
    # watches for that email or clicks that link, so even a correct on-page
    # acknowledgment check here would only ever confirm step 1 ("request
    # received"), never that a listing was actually removed. Fixing this
    # properly means wiring an email-confirmation step (Watson already has
    # Gmail polling infra in jobs/email_intake.py / jobs/email_reply/) before
    # status='submitted' can be trusted at face value for any broker shaped
    # like this — not just a missing text-match on this return statement.
    return True, None


def submit_removal(removal_id: int, dry_run: bool = False) -> dict:
    removal = _load_removal(removal_id)
    if not removal:
        return {"ok": False, "error": "removal not found"}

    if dry_run:
        # Diagnostic-only path: never checks/changes status, never sends
        # Telegram — used to inspect a broker's reCAPTCHA behavior before
        # deciding (at review, not here) whether to activate it.
        if removal["opt_out_method"] != "form":
            return {"ok": False, "error": "dry_run only applies to opt_out_method='form'"}
        return asyncio.run(_submit_form(removal, dry_run=True))

    if removal["status"] not in ("pending", "approved", "failed"):
        return {"ok": False, "error": f"removal already {removal['status']}"}

    method = removal["opt_out_method"]
    if method == "form":
        ok, reason = asyncio.run(_submit_form(removal))
    elif method == "email":
        result = send_email(
            to_email=removal["opt_out_target"],
            to_name=removal["broker_name"],
            subject=f"Data removal request — {removal['person_name']}",
            text_body=(
                "To Whom It May Concern,\n\n"
                f"Please remove the listing for {removal['person_name']} found at:\n"
                f"{removal['matched_url']}\n\n"
                "This is a request to remove this personal information from your service.\n\n"
                "Thank you,\nWatson, on behalf of Dr. Bill Yomes"
            ),
        )
        ok, reason = result["success"], result.get("error")
    elif method == "mail":
        ok, reason = False, "mail opt-out not automated in v1 — needs manual mail request"
    else:
        ok, reason = False, f"unknown opt_out_method: {method}"

    if ok:
        _mark_submitted(removal_id)
        send_telegram(f"✅ Privacy Guard: removal submitted — {removal['person_name']} on {removal['broker_name']}.")
        return {"ok": True}
    else:
        _mark_failed(removal_id, reason or "unknown failure")
        send_telegram(
            f"⚠️ Privacy Guard: removal FAILED — {removal['person_name']} on {removal['broker_name']}: {reason}"
        )
        return {"ok": False, "error": reason}


def skip_removal(removal_id: int) -> dict:
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE privacy_removals SET status='rejected' WHERE id=? AND status='pending'",
            (removal_id,),
        )
        conn.commit()
        return {"ok": cur.rowcount > 0}
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Submit one Privacy Guard removal request.")
    parser.add_argument("--removal-id", type=int, required=True)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="form brokers only: fill the form but stop before clicking submit; "
             "report reCAPTCHA/challenge signals instead. Never touches DB status.",
    )
    args = parser.parse_args()
    print(submit_removal(args.removal_id, dry_run=args.dry_run))
