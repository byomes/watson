"""Email templates for Givebutter donor thank-yous."""


def first_gift_email(donor_name: str, amount: float) -> tuple[str, str]:
    """Return (subject, html_body) for a first-time donor."""
    first_name = donor_name.split()[0] if donor_name else "Friend"
    subject = f"Thank you for your gift, {first_name}"
    html_body = f"""\
<p>Dear {first_name},</p>

<p>I want to personally thank you for your gift of ${amount:.2f} to Faith Makes Sense. \
This isn't a small thing — your generosity makes it possible for us to help people think \
clearly about faith and engage the hard questions honestly.</p>

<p>Too many believers feel unprepared when doubt or difficult conversations arise. \
Faith Makes Sense exists to change that, and your gift is part of why we can. \
Welcome to something I believe matters deeply.</p>

<p>With genuine gratitude,<br>
Dr. Bill Yomes<br>
Faith Makes Sense</p>"""
    return subject, html_body


def repeat_gift_email(donor_name: str, amount: float, gift_count: int) -> tuple[str, str]:
    """Return (subject, html_body) for a repeat donor."""
    first_name = donor_name.split()[0] if donor_name else "Friend"
    subject = f"You gave again — thank you, {first_name}"
    html_body = f"""\
<p>Dear {first_name},</p>

<p>This is your {_ordinal(gift_count)} gift to Faith Makes Sense, and I don't take that \
lightly. ${amount:.2f} — thank you. Donors who keep showing up are the reason this work \
keeps going.</p>

<p>People come to us doubting, confused, or simply wanting to think more rigorously about \
what they believe. Because of you, they don't have to do that alone. That means something \
real to me, and I hope it does to you too.</p>

<p>With deep appreciation,<br>
Dr. Bill Yomes<br>
Faith Makes Sense</p>"""
    return subject, html_body


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
