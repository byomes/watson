"""Email templates for Givebutter donor thank-yous."""


def first_gift_email(donor_name: str, amount: float) -> tuple[str, str]:
    """Return (subject, html_body) for a first-time donor."""
    first_name = donor_name.split()[0] if donor_name else "Friend"
    subject = f"Thank you for your gift, {first_name}"
    html_body = f"""\
<p>Dear {first_name},</p>

<p>Thank you for your gift of ${amount:.2f} to Faith Makes Sense. We're grateful — \
generosity like yours is what makes this ministry possible.</p>

<p>Faith Makes Sense exists to help people think clearly about faith, engage hard questions \
honestly, and find that belief and reason belong together. Your support puts that work in \
front of people who need it.</p>

<p>Welcome to the FMS community. We're glad you're with us.</p>

<p>With gratitude,<br>
The FMS Team<br>
Faith Makes Sense</p>"""
    return subject, html_body


def repeat_gift_email(donor_name: str, amount: float, gift_count: int) -> tuple[str, str]:
    """Return (subject, html_body) for a repeat donor."""
    first_name = donor_name.split()[0] if donor_name else "Friend"
    subject = f"You gave again — thank you, {first_name}"
    html_body = f"""\
<p>Dear {first_name},</p>

<p>This is your {_ordinal(gift_count)} gift to Faith Makes Sense — thank you. \
Your continued support means more than a transaction. It means you believe in what \
we're doing, and that keeps us going.</p>

<p>Because of donors like you, we can keep creating resources that help people \
wrestle honestly with faith. That work matters, and you're part of it.</p>

<p>With deep appreciation,<br>
The FMS Team<br>
Faith Makes Sense</p>"""
    return subject, html_body


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
