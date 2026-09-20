"""Email templates for Givebutter donor thank-yous.

The middle paragraph (what FMS is up to right now) isn't hardcoded here --
it's pulled from this month's update, asked of Bill via Telegram on the
first Monday of every month (jobs/givebutter/monthly_update.py). That's
what keeps this from going stale between asks (see bug: the Sept 20
"final stretch before... Sept 15" text, still referencing a book that had
already launched). No update on file for the current month falls back to
_STANDARD_PARAGRAPH below, a generic thank-you with no specific campaign
mention.
"""
import html

from jobs.givebutter.monthly_update import get_current_update

_STANDARD_PARAGRAPH = (
    "Every gift you send goes straight into the work, training pastors in East "
    "Africa and creating resources that help people wrestle honestly with what "
    "they believe. None of it happens without people like you choosing to be "
    "part of it."
)


def _current_paragraph() -> str:
    """This month's update, HTML-escaped with line breaks preserved, or the
    standard fallback paragraph if Bill hasn't shared anything new this
    month (or replied skip)."""
    update = get_current_update()
    if not update:
        return _STANDARD_PARAGRAPH
    # quote=False: this text only ever lands inside a <p> body, never an
    # attribute, and escaping quotes turns them into &#x27; entities that
    # show up literally in the Telegram review preview (_html_to_text in
    # notify.py strips tags but doesn't decode entities).
    return html.escape(update, quote=False).replace("\n", "<br>\n")


def first_gift_email(donor_name: str, amount: float) -> tuple[str, str]:
    """Return (subject, html_body) for a first-time donor."""
    first_name = donor_name.split()[0] if donor_name else "Friend"
    subject = f"Thank you, {first_name}"
    html_body = f"""\
<p>Dear {first_name},</p>

<p>Thank you for partnering with Faith Makes Sense. It means a lot to know you're joining us \
in the work.</p>

<p>{_current_paragraph()}</p>

<p>We're glad you're with us.</p>

<p>Thank you again,<br>
The FMS Team<br>
Faith Makes Sense</p>"""
    return subject, html_body


def repeat_gift_email(donor_name: str, amount: float, gift_count: int) -> tuple[str, str]:
    """Return (subject, html_body) for a repeat donor."""
    first_name = donor_name.split()[0] if donor_name else "Friend"
    subject = f"Thank you, {first_name}"
    html_body = f"""\
<p>Dear {first_name},</p>

<p>Thank you for your continued support of Faith Makes Sense. Every time you give, it's a \
reminder that this work isn't happening in isolation. People like you are choosing to be \
part of it.</p>

<p>{_current_paragraph()}</p>

<p>We're grateful you're walking alongside us in this.</p>

<p>Thank you again,<br>
The FMS Team<br>
Faith Makes Sense</p>"""
    return subject, html_body
