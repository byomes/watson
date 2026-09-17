"""jobs/congregation/married_age_check.py -- Weekly sanity check: any
active member tagged household_role 'husband'/'wife' whose birthdate
computes to under 18 gets flagged to Donna for a birth-year correction.
Per Bill (2026-09-16): a married role is trustworthy on its own, so
"married but looks like a kid" means the birth year was mistyped, not
the role -- this is the standing version of that one-off rule, not a
duplicate of it. Sends nothing when there's nothing to flag.

Runs Tuesday 9am per Bill's 2026-09-16 rule that any email to Donna
goes out Tue/Wed/Thu at 9am (moved off the original 7:35am slot to
match; Tuesday is still one of the allowed days, so only the time
changed).

Cron:
  0 9 * * 2  PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python3 \
    -m jobs.congregation.married_age_check \
    >> /home/billyomes/watson/logs/married_age_check.log 2>&1
"""
import logging

from jobs.congregation.age_groups import find_implausible_marriages
from jobs.email_job.brevo_send import send_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [married_age_check] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DONNA_EMAIL = "donna@catalyst302.com"
DONNA_NAME = "Donna Redman"


def build_body(flagged: list[dict]) -> tuple[str, str]:
    lines = [
        "Hi Donna,",
        "",
        "These members are marked as married but their birth year on file makes them under 18. "
        "The role is almost certainly right and the birth year is the typo, so if you have a minute:",
        "",
    ]
    for m in flagged:
        lines.append(f"- {m['name']} ({m['household_role']}): birthdate {m['birthdate']}, computes to age {m['age']}")
    lines += ["", "Thanks,"]
    text_body = "\n".join(lines)
    html_body = "<br>".join(l if l else "<br>" for l in lines)
    return text_body, html_body


def main() -> None:
    flagged = find_implausible_marriages()
    if not flagged:
        log.info("No implausible married-age members found, nothing to send.")
        return

    text_body, html_body = build_body(flagged)
    send_email(
        to_email=DONNA_EMAIL,
        to_name=DONNA_NAME,
        subject="Birth years to double-check",
        text_body=text_body,
        html_body=html_body,
        tags=["congregation_data_correction"],
    )
    log.info(f"Flagged {len(flagged)} member(s) to Donna: {[m['name'] for m in flagged]}")


if __name__ == "__main__":
    main()
