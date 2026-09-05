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

KNOWN GAP (project_backlog id=37), now surfaced rather than hidden:
_submit_form()'s single-step success path is still "the click didn't
throw", not a verified confirmation signal — see the comment at its return
statement. Rather than mark that status='submitted' (indistinguishable
from a genuinely verified removal), submit_removal() now marks it
status='unconfirmed' — see _mark_unconfirmed(). _submit_wizard()
(multi-step brokers) does NOT have this gap by construction: it refuses to
click a final submit button at all unless the broker's form_selectors
carries a success_check — see _submit_wizard()'s docstring — so a wizard
success is always genuinely confirmed.

opt_out_method='email' (BeenVerified) is a separate case, out of scope for
this pass: send_email()'s success just means Brevo accepted the send, not
that the broker acted on it — arguably the same "we don't actually know"
gap, but not the literal "click didn't throw" case this pass addresses.
Still marked status='submitted' on send success, unchanged. (2026-09-05:
jobs/privacy/email_ack.py now notifies on an inbound reply for this case,
but deliberately never auto-upgrades status from a free-text reply — see
that module's docstring.)

CAPTCHA-gated brokers (2026-09-05, project_backlog id=37/38's third path):
_submit_form() and _submit_wizard() both check for
form_selectors["captcha_assist"] before ever refusing/clicking blind on a
CAPTCHA-gated final step — see jobs/privacy/captcha_assist.py. Bill solves
the challenge himself over a Tailscale-only chrome://inspect mirror of the
same (still headless=True) page, then taps a Telegram button; Watson never
clicks the final submit itself on that path.
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
from jobs.privacy import captcha_assist, send_telegram
from jobs.privacy.verify import check_success

log = logging.getLogger(__name__)

LOG_DIR = Path.home() / "watson" / "logs" / "privacy"


def _load_removal(removal_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT r.*, p.name AS person_name, p.birth_year AS person_birth_year,
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


def _mark_unconfirmed(removal_id: int) -> None:
    """Distinct from _mark_submitted(): the opt-out click succeeded (didn't
    throw) but this broker's flow gives no independently verifiable success
    signal (see _submit_form's KNOWN GAP, module docstring). Still scheduled
    for rescan on the same cadence as a confirmed submission — the listing
    may well be gone — but the status stays visibly different so Bill knows
    not to trust it at face value."""
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE privacy_removals
               SET status='unconfirmed', submitted_at=datetime('now'),
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

    Non-dry-run return: (ok: bool, error: str | None, confirmed: bool). confirmed
    is only meaningful when ok=True, and is always False here — a bare click
    that didn't throw is never treated as a verified success (see the KNOWN
    GAP comment at the bottom of this function).
    Dry-run return: dict, always with "ok" and "dry_run": True.
    """
    selectors = json.loads(removal["form_selectors"] or "{}")
    submit_button = selectors.get("submit_button")
    if not removal["opt_out_target"] or not submit_button:
        msg = "broker not fully verified (missing opt_out_target/submit_button selector)"
        return {"ok": False, "dry_run": True, "error": msg} if dry_run else (False, msg, False)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    challenge_requests = []
    console_messages = []

    # captcha_assist brokers (e.g. MyLife) need the remote-debugging port set
    # at browser LAUNCH time, so this has to be decided before entering
    # get_page()'s context — see captcha_assist.py's module docstring for
    # why this is safe with headless=True unchanged.
    assist = bool(selectors.get("captcha_assist")) and not dry_run
    port = captcha_assist.pick_port() if assist else None
    page_kwargs = {"launch_args": captcha_assist.launch_args(port)} if assist else {}

    async with get_page(**page_kwargs) as page:
        if dry_run:
            page.on("request", lambda req: (
                challenge_requests.append(req.url)
                if any(h in req.url for h in _CHALLENGE_HOST_HINTS) else None
            ))
            page.on("console", lambda msg: console_messages.append(f"{msg.type}: {msg.text}"))

        ok = await goto_safe(page, removal["opt_out_target"], wait_until="networkidle")
        if not ok:
            msg = "could not load opt-out page (robots.txt disallow or navigation failure)"
            return {"ok": False, "dry_run": True, "error": msg} if dry_run else (False, msg, False)
        try:
            # Only the field shapes actually confirmed common across these
            # brokers' opt-out forms during Phase 2 verification are handled
            # here (name / profile-URL / confirmation-email, plus the
            # additional split-name/location/birth-year shape MyLife's form
            # needs, added 2026-09-05 for captcha_assist) — a broker whose
            # form needs something else stays unverified (active=0) rather
            # than this code guessing at unknown selectors. zip is
            # deliberately never filled: family_profiles has no zip column
            # and nothing here invents one.
            if selectors.get("name_field"):
                await page.fill(selectors["name_field"], removal["person_name"])
            if selectors.get("url_field"):
                await page.fill(selectors["url_field"], removal["matched_url"] or "")
            if selectors.get("email_field"):
                await page.fill(selectors["email_field"], os.getenv("WATSON_GMAIL_ADDRESS", ""))
            if selectors.get("consent_checkbox"):
                # force=True: PeopleConnect's styled checkbox has a <label>
                # positioned on top of the real <input> (confirmed live
                # 2026-09-05 -- Playwright's actionability check correctly
                # flags the label as intercepting pointer events, since
                # that's genuinely true of the DOM). The label's own `for`
                # id is a per-request-random UUID, not a stable selector to
                # click instead -- force=True dispatches the click directly
                # on the real input, which is what the label would forward
                # to anyway, without depending on that unstable id.
                await page.check(selectors["consent_checkbox"], force=True)
            parts = (removal["person_name"] or "").split()
            first, last = (parts[0], parts[-1]) if len(parts) > 1 else (removal["person_name"], "")
            if selectors.get("first_name_field"):
                await page.fill(selectors["first_name_field"], first)
            if selectors.get("last_name_field"):
                await page.fill(selectors["last_name_field"], last)
            matched = json.loads(removal["matched_fields"] or "{}")
            if selectors.get("state_field") and matched.get("state"):
                await page.fill(selectors["state_field"], matched["state"])
            if selectors.get("city_field") and matched.get("city"):
                await page.fill(selectors["city_field"], matched["city"])
            if selectors.get("birth_year_field") and removal.get("person_birth_year"):
                await page.fill(selectors["birth_year_field"], str(removal["person_birth_year"]))

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

            if assist:
                # Everything's filled and the page is ready to solve+submit
                # — hand off to Bill instead of clicking submit_button
                # ourselves. See captcha_assist.py's module docstring.
                ok2, reason, confirmed = await captcha_assist.wait_for_human(
                    removal["id"], removal["person_name"], removal["broker_name"],
                    port, selectors.get("success_check"),
                )
                if not ok2:
                    return False, reason, False
                return await captcha_assist.resolve_after_human(page, removal, selectors.get("success_check"))

            await page.click(submit_button)
            await page.wait_for_timeout(2000)

            # error_check (2026-09-05, found live testing PeopleConnect's
            # suppression tool): "the click didn't throw" can still mean the
            # backend rejected the request -- confirmed live for USSearch,
            # whose step-1 form shows a literal "Something went wrong,
            # please try again" after a real POST to suppression-api.
            # peopleconnect.us/v1/users gets rejected (Cloudflare's invisible
            # challenge-platform JS loads on this page -- a bot-management
            # gate with no visible CAPTCHA, same practical category as every
            # other CAPTCHA-gated broker, just a different mechanism).
            # error_check uses the exact same shape as success_check
            # (verify.py's check_success) but inverted: a match here means a
            # confirmed FAILURE, not a silent unconfirmed pass.
            error_check = selectors.get("error_check")
            if error_check:
                is_error, _ = await check_success(page, error_check)
                if is_error:
                    shot_path = LOG_DIR / f"removal-{removal['id']}-{datetime.now():%Y%m%d%H%M%S}.png"
                    try:
                        await page.screenshot(path=str(shot_path))
                    except Exception:
                        pass
                    # dry_run never reaches this line (it returns earlier),
                    # so the plain tuple shape is always correct here.
                    return False, f"broker showed a real error after submit (screenshot: {shot_path})", False
        except Exception as exc:
            shot_path = LOG_DIR / f"removal-{removal['id']}-{datetime.now():%Y%m%d%H%M%S}.png"
            try:
                await page.screenshot(path=str(shot_path))
            except Exception:
                pass
            log_browser_failure("privacy.remove form submit", removal["opt_out_target"], exc)
            msg = f"form submission failed (screenshot: {shot_path})"
            return {"ok": False, "dry_run": True, "error": msg} if dry_run else (False, msg, False)
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
    # a broker shaped like this could ever earn confirmed=True. Until then,
    # submit_removal() marks this status='unconfirmed', not 'submitted'.
    return True, None, False


def _resolve_field_value(field_key: str, removal: dict, matched: dict) -> str | None:
    """Maps a canonical field-key (used in a step's "fields" dict) to the
    real value to fill. Same small, deliberately-closed set of field shapes
    as _submit_form — a broker needing something else stays unverified
    rather than this code guessing at unknown data."""
    if field_key == "name":
        return removal["person_name"]
    if field_key in ("first_name", "last_name"):
        parts = (removal["person_name"] or "").split()
        first, last = (parts[0], parts[-1]) if len(parts) > 1 else (removal["person_name"], "")
        return first if field_key == "first_name" else last
    if field_key == "url":
        return removal["matched_url"] or ""
    if field_key == "email":
        return os.getenv("WATSON_GMAIL_ADDRESS", "")
    if field_key in ("city", "state"):
        return matched.get(field_key) or ""
    return None


async def _submit_wizard(removal: dict, dry_run: bool = False):
    """Drives a multi-step opt-out wizard: fill a step's fields -> click its
    next_button -> wait for the next step to actually render -> repeat,
    ending on the final step's submit_button — which this function refuses
    to click at all unless that step's form_selectors carries a verified
    success_check (see jobs/privacy/verify.py's check_success). This is the fix for the gap found
    on Spokeo (project_backlog id=37): "the click didn't throw" is never
    treated as success here, by construction, not by convention.

    form_selectors shape for a wizard broker:
      {"steps": [
          {"fields": {"<key>": "<css selector>", ...},
           "next_button": "<css selector>",
           "wait_for": "<css selector of the NEXT step's marker>"},   # optional but recommended
          ...
          {"fields": {...},
           "submit_button": "<css selector>",
           "success_check": {"type": "...", "value": "..."}}
      ]}

    dry_run=True walks every step's fields and every next_button up to (but
    never including) the final step's submit_button, then returns
    diagnostics — used to verify the selectors/step sequence actually work
    live before ever considering activation. It never clicks a
    next_button or submit_button that isn't already known-safe from prior
    live research (see the PR this shipped in for how each broker's next
    buttons were confirmed to be pure client-side step transitions, not a
    real backend call, before this code was allowed to click them).

    HARD PRECONDITION before configuring ANY broker with a "steps" list
    (project_backlog id=38 — Whitepages and Radaris both real-world-proved
    this the hard way): this function assumes every non-final next_button
    click is a safe, pure-client-side transition. That assumption is NOT
    universal — Whitepages' very first "Next" already creates a real
    server-side removal request, and Radaris has a real backend POST at an
    intermediate step (not the final one). Verify EVERY step's advance
    action individually (network-monitor the click, or read the button's
    JS handler source directly, per the recon approach in project_backlog
    id=38) before adding it here — checking only the final submit_button is
    not enough. A broker whose steps branch (different backend endpoints
    depending on user input, e.g. Radaris' step 4) doesn't fit this
    function's linear model at all; don't force it in.

    Non-dry-run return: (ok: bool, error: str | None, confirmed: bool) — same
    shape as _submit_form, except confirmed is True on a genuine success (the
    only way this function ever returns ok=True is after _check_success()
    passed). Dry-run return: dict, always with "ok" and "dry_run": True.
    """
    selectors = json.loads(removal["form_selectors"] or "{}")
    steps = selectors.get("steps")
    if not steps:
        msg = "broker not configured for multi-step submission (no 'steps' in form_selectors)"
        return {"ok": False, "dry_run": True, "error": msg} if dry_run else (False, msg, False)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    matched = json.loads(removal["matched_fields"] or "{}")
    step_log: list[str] = []

    # Same reasoning as _submit_form(): a captcha_assist final step needs the
    # remote-debugging port set at launch time, decided before entering
    # get_page()'s context.
    assist = bool(steps[-1].get("captcha_assist")) and not dry_run
    port = captcha_assist.pick_port() if assist else None
    page_kwargs = {"launch_args": captcha_assist.launch_args(port)} if assist else {}

    async with get_page(**page_kwargs) as page:
        ok = await goto_safe(page, removal["opt_out_target"], wait_until="networkidle")
        if not ok:
            msg = "could not load opt-out page (robots.txt disallow or navigation failure)"
            return {"ok": False, "dry_run": True, "error": msg} if dry_run else (False, msg, False)

        try:
            for i, step in enumerate(steps):
                is_last = i == len(steps) - 1
                for field_key, sel in step.get("fields", {}).items():
                    value = _resolve_field_value(field_key, removal, matched)
                    if value:
                        await page.fill(sel, str(value))

                if not is_last:
                    next_button = step.get("next_button")
                    if next_button:
                        await page.click(next_button)
                        wait_selector = step.get("wait_for") or next(
                            iter(steps[i + 1].get("fields", {}).values()), None
                        )
                        if wait_selector:
                            await page.wait_for_selector(wait_selector, timeout=10000)
                        else:
                            await page.wait_for_timeout(step.get("wait_ms", 1500))
                    step_log.append(f"step {i + 1}/{len(steps)}: advanced")
                    continue

                # Final step.
                submit_button = step.get("submit_button")
                success_check = step.get("success_check")
                if not submit_button:
                    msg = "broker not fully verified (missing final submit_button selector)"
                    return {"ok": False, "dry_run": True, "error": msg} if dry_run else (False, msg, False)

                if dry_run:
                    shot_path = LOG_DIR / f"dryrun-wizard-{removal['id']}-{datetime.now():%Y%m%d%H%M%S}.png"
                    await page.screenshot(path=str(shot_path), full_page=True)
                    return {
                        "ok": True,
                        "dry_run": True,
                        "reached_final_step": True,
                        "step_log": step_log,
                        "has_success_check": success_check is not None,
                        "screenshot": str(shot_path),
                    }

                if assist:
                    # Bill solves the CAPTCHA and clicks submit_button
                    # himself over the mirror — same handoff as
                    # _submit_form(), see captcha_assist.py's module
                    # docstring. A missing success_check here is not a hard
                    # refusal like the branch below: Bill's own real click
                    # already happened, so this can only land as
                    # ok=True/confirmed=False (unconfirmed), never a refusal.
                    ok2, reason, confirmed = await captcha_assist.wait_for_human(
                        removal["id"], removal["person_name"], removal["broker_name"], port, success_check,
                    )
                    if not ok2:
                        return False, reason, False
                    return await captcha_assist.resolve_after_human(page, removal, success_check)

                if not success_check:
                    # Hard refusal, not a soft warning — same principle as
                    # Spokeo staying active=0 rather than trusting a bare click.
                    return False, "no verified success_check configured — refusing to submit on click alone", False

                await page.click(submit_button)
                await page.wait_for_timeout(step.get("wait_ms", 2500))
                success, reason = await check_success(page, success_check)
                if not success:
                    shot_path = LOG_DIR / f"removal-{removal['id']}-{datetime.now():%Y%m%d%H%M%S}.png"
                    try:
                        await page.screenshot(path=str(shot_path))
                    except Exception:
                        pass
                    return False, reason or f"submitted, but success check failed (screenshot: {shot_path})", False
        except Exception as exc:
            shot_path = LOG_DIR / f"removal-{removal['id']}-{datetime.now():%Y%m%d%H%M%S}.png"
            try:
                await page.screenshot(path=str(shot_path))
            except Exception:
                pass
            log_browser_failure("privacy.remove wizard submit", removal["opt_out_target"], exc)
            msg = f"wizard submission failed at step {len(step_log) + 1}/{len(steps)} (screenshot: {shot_path})"
            return {"ok": False, "dry_run": True, "error": msg} if dry_run else (False, msg, False)

    return True, None, True


def submit_removal(removal_id: int, dry_run: bool = False) -> dict:
    removal = _load_removal(removal_id)
    if not removal:
        return {"ok": False, "error": "removal not found"}

    if dry_run:
        # Diagnostic-only path: never checks/changes status, never sends
        # Telegram — used to inspect a broker's reCAPTCHA behavior, or walk
        # a wizard's steps, before deciding (at review, not here) whether
        # to activate it.
        if removal["opt_out_method"] != "form":
            return {"ok": False, "error": "dry_run only applies to opt_out_method='form'"}
        selectors = json.loads(removal["form_selectors"] or "{}")
        submit_fn = _submit_wizard if "steps" in selectors else _submit_form
        return asyncio.run(submit_fn(removal, dry_run=True))

    if removal["status"] not in ("pending", "approved", "failed"):
        return {"ok": False, "error": f"removal already {removal['status']}"}

    method = removal["opt_out_method"]
    if method == "form":
        selectors = json.loads(removal["form_selectors"] or "{}")
        submit_fn = _submit_wizard if "steps" in selectors else _submit_form
        ok, reason, confirmed = asyncio.run(submit_fn(removal))
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
        # Out of scope for this pass (see module docstring): email delivery
        # success still counts as 'submitted', same as before.
        confirmed = True
    elif method == "mail":
        ok, reason, confirmed = False, "mail opt-out not automated in v1 — needs manual mail request", False
    else:
        ok, reason, confirmed = False, f"unknown opt_out_method: {method}", False

    if ok:
        if confirmed:
            _mark_submitted(removal_id)
            send_telegram(f"✅ Privacy Guard: removal submitted — {removal['person_name']} on {removal['broker_name']}.")
        else:
            _mark_unconfirmed(removal_id)
            send_telegram(
                f"⚠️ Privacy Guard: removal request SENT but UNCONFIRMED — "
                f"{removal['person_name']} on {removal['broker_name']}. The submit click succeeded, but "
                "this broker gives no way to verify real completion — treat as pending until checked manually."
            )
        return {"ok": True, "confirmed": confirmed}
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
