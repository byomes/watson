"""jobs/congregation/_oneoff_donna_birthyear_note.py -- One-shot: email
Donna Redman asking her to correct Kathryn Taylor's and Rose Spinelli's
birth years (both are married adult women but their birthdate on file
computes to age 2 and age 10 -- Bill confirmed the role is right and
the year is what's wrong, 2026-09-16). Self-deletes crontab line + this
file after sending, same pattern as the 2026-09-11 Donna Redman one-off
and the trading week-1 summary one-off. Scheduled 2026-09-17 09:30 ET
per Bill.
"""
import subprocess

from jobs.congregation.age_groups import _conn
from jobs.email_job.brevo_send import send_email

CRON_MARKER = "jobs.congregation._oneoff_donna_birthyear_note"

DONNA_EMAIL = "donna@catalyst302.com"
DONNA_NAME = "Donna Redman"

NAMES = ["Kathryn Taylor", "Rose Spinelli"]


def _current_birthdates() -> dict:
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT name, birthdate FROM members WHERE name IN "
            f"({','.join('?' * len(NAMES))})",
            NAMES,
        ).fetchall()
    return {r["name"]: r["birthdate"] for r in rows}


def build_body() -> tuple[str, str]:
    on_file = _current_birthdates()
    lines = [
        "Hi Donna,",
        "",
        "Two birth years in the congregation database look off and could use your correction:",
        "",
    ]
    for name in NAMES:
        bd = on_file.get(name) or "(none on file)"
        lines.append(f"- {name}: currently {bd}")
    lines += [
        "",
        "Both are married women, so the year on file is almost certainly a typo rather than the "
        "role being wrong. Whenever you have the correct birth years, just reply with them or update "
        "them the usual way and I'll take it from there.",
        "",
        "Thanks,",
    ]
    text_body = "\n".join(lines)
    html_body = "<br>".join(l if l else "<br>" for l in lines)
    return text_body, html_body


def self_delete() -> None:
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True).stdout
    kept_lines = [
        ln for ln in current.splitlines()
        if CRON_MARKER not in ln and "One-off: Donna Redman birth-year correction note" not in ln
    ]
    new_crontab = "\n".join(kept_lines) + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)

    import os
    os.remove(__file__)


if __name__ == "__main__":
    text_body, html_body = build_body()
    send_email(
        to_email=DONNA_EMAIL,
        to_name=DONNA_NAME,
        subject="Two birth years to double-check",
        text_body=text_body,
        html_body=html_body,
        tags=["congregation_data_correction"],
    )
    self_delete()
