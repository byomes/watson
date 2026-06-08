"""Report menu — routes names/numbers to report functions."""
import re
from html.parser import HTMLParser

from jobs.connect_cards import pastoral_reports as _pr

REPORTS = [
    ("next_steps",          "Next Steps",                  _pr.next_steps_report),
    ("missed_weeks",        "Absent Members",              _pr.missed_weeks_report),
    ("first_time_visitors", "First-Time Visitors",         _pr.first_time_visitors_report),
    ("attendance_trends",   "Attendance Trends",           _pr.attendance_trends_report),
    ("overview",            "Congregation Overview",       _pr.congregation_overview_report),
]


def get_menu_html() -> str:
    items = "".join(
        f"<li style='margin:6px 0'>"
        f"<a href='#' onclick=\"sendPrompt('run report: {name}');return false\" "
        f"style='color:#4a90d9;text-decoration:none'>{i+1}. {label}</a>"
        f"</li>"
        for i, (name, label, _) in enumerate(REPORTS)
    )
    return (
        "<strong>Pastoral Reports</strong><br>"
        f"<ol style='padding-left:18px;margin:8px 0'>{items}</ol>"
        "<small style='color:#888'>Click a report or type <em>run report: &lt;name&gt;</em></small>"
    )


def run_report(key: str, weeks: int | None = None) -> tuple[str, str]:
    key_clean = key.strip().lower()
    match = None
    for i, (slug, label, fn) in enumerate(REPORTS):
        if (
            key_clean == slug
            or key_clean == label.lower()
            or key_clean == str(i + 1)
        ):
            match = (slug, fn)
            break
    if match is None:
        for slug, label, fn in REPORTS:
            if key_clean in slug or key_clean in label.lower():
                match = (slug, fn)
                break
    if match is None:
        raise ValueError(f"No report found matching '{key}'")
    slug, fn = match
    _defaults = {
        "next_steps":          12,
        "missed_weeks":        3,
        "first_time_visitors": 4,
        "attendance_trends":   8,
    }
    if slug in _defaults:
        return fn(weeks=weeks or _defaults[slug])
    return fn()


def get_telegram_menu() -> str:
    lines = ["*Pastoral Reports*\n"]
    for i, (name, label, _) in enumerate(REPORTS):
        lines.append(f"{i+1}. {label}")
    lines.append("\nReply: `report <name or number>`")
    return "\n".join(lines)


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str):
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def report_to_telegram(html: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(html)
    text = stripper.get_text()
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > 4000:
        text = text[:3990] + "\n…(truncated)"
    return text
