"""Email templates for Givebutter donor thank-yous."""


def first_gift_email(donor_name: str, amount: float) -> tuple[str, str]:
    """Return (subject, html_body) for a first-time donor."""
    first_name = donor_name.split()[0] if donor_name else "Friend"
    subject = f"Thank you, {first_name}"
    html_body = f"""\
<p>Dear {first_name},</p>

<p>Thank you for partnering with Faith Makes Sense. It means a lot to know you're joining us \
in the work.</p>

<p>Right now we're in the final stretch before launching The Wrong Jesus, Dr. Bill's \
new book releasing September 15. It's the kind of resource we hope reaches people \
who are wrestling honestly with who Jesus actually is, not the version they've \
inherited, but the one Scripture actually presents. Your support helps make \
projects like this possible.</p>

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

<p>Right now we're in the final stretch before launching The Wrong Jesus, Dr. Bill's \
new book releasing September 15. It's the kind of resource we hope reaches people \
who are wrestling honestly with who Jesus actually is, not the version they've \
inherited, but the one Scripture actually presents. Your support helps make \
projects like this possible.</p>

<p>We're grateful you're walking alongside us in this.</p>

<p>Thank you again,<br>
The FMS Team<br>
Faith Makes Sense</p>"""
    return subject, html_body
