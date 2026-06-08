"""Report menu — routes names/numbers to report functions."""
import re
from html.parser import HTMLParser

from jobs.connect_cards import pastoral_reports as _pr

REPORTS = [
    ("next_steps",          "Next Steps",                  _pr.next_steps_report),
    ("missed_weeks",        "Missed Weeks",                _pr.missed_weeks_report),
    ("first_time_visitors", "First-Time Visitors",         _pr.first_time_visitors_report),
    ("lapsed_visitors",     "Visitors Not Seen Since",     _pr.lapsed_visitors_report),
    ("next_steps_followup", "Next Steps Follow-Up",        _pr.next_steps_followup_report),
    ("new_faces",           "New Faces",                   _pr.new_faces_report),
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


def run_report(name: str) -> tuple[str, str] | None:
    name_clean = name.strip().lower()
    for i, (slug, label, fn) in enumerate(REPORTS):
        if (
            name_clean == slug
            or name_clean == label.lower()
            or name_clean == str(i + 1)
        ):
            return fn()
    for slug, label, fn in REPORTS:
        if name_clean in slug or name_clean in label.lower():
            return fn()
    return None


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
