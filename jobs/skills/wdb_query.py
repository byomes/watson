"""wdb_query.py — natural language pattern matching against watson.db for leadership intelligence."""
import sqlite3
from pathlib import Path

WATSON_DB = Path(__file__).resolve().parents[2] / "data" / "watson.db"
MAX_ROWS = 20

_NO_MATCH = (
    "I don't have a pattern for that leadership query yet. "
    "Try: stalled tasks, task completion, inactive leaders, notes gaps, "
    "follow-ups, recent meetings, or team overview."
)


def _format_rows(rows, description) -> str:
    total = len(rows)
    display = rows[:MAX_ROWS]
    col_names = [d[0] for d in description]
    lines = []
    for row in display:
        parts = [f"{col}: {val}" for col, val in zip(col_names, row) if val is not None]
        lines.append("• " + " | ".join(parts))
    result = "\n".join(lines)
    if total > MAX_ROWS:
        result += f"\n\nShowing {MAX_ROWS} of {total} results."
    return result


def _pattern_match(question: str) -> str | None:
    """Return SQL for a recognized leadership query pattern, or None."""
    q = question.lower().strip()

    # Check order: team_overview → stalled → completion_rate → inactive → notes_gap → follow_up → recent_meetings → tasks_by_leader

    # TEAM OVERVIEW
    if any(w in q for w in ['team overview', 'team summary', 'how is the team', 'team status', 'leadership team']):
        return (
            "SELECT tm.name, tm.role, tm.status, tm.last_activity_date, COUNT(tt.id) as open_tasks "
            "FROM team_members tm LEFT JOIN team_tasks tt ON tt.member_id = tm.id AND tt.status = 'open' "
            "WHERE tm.active = 1 GROUP BY tm.id ORDER BY tm.sort_order ASC"
        )

    # STALLED TASKS
    if any(w in q for w in ['stalled', 'overdue', 'late tasks', 'past due', 'not completed', 'open tasks', 'pending tasks']):
        return (
            "SELECT tm.name, tt.title, tt.due_date, tt.priority "
            "FROM team_tasks tt JOIN team_members tm ON tt.member_id = tm.id "
            "WHERE tt.status = 'open' AND tt.due_date < date('now') AND tm.active = 1 "
            "ORDER BY tt.due_date ASC"
        )

    # TASK COMPLETION RATE
    if any(w in q for w in ['completion rate', 'how many completed', 'tasks completed', 'finished tasks', 'who is completing']):
        return (
            "SELECT tm.name, "
            "COUNT(CASE WHEN tt.status = 'open' THEN 1 END) as open, "
            "COUNT(CASE WHEN tt.status = 'completed' THEN 1 END) as done "
            "FROM team_tasks tt JOIN team_members tm ON tt.member_id = tm.id "
            "WHERE tm.active = 1 GROUP BY tm.name ORDER BY done DESC"
        )

    # INACTIVE LEADERS
    if any(w in q for w in ['inactive', 'no activity', 'not active', 'quiet', 'no recent', 'disengaged', "haven't heard"]):
        return (
            "SELECT name, last_activity_date, role FROM team_members "
            "WHERE active = 1 AND (last_activity_date IS NULL OR last_activity_date < date('now', '-30 days')) "
            "ORDER BY last_activity_date ASC"
        )

    # SHARED NOTES GAPS
    if any(w in q for w in ['no notes', 'notes gap', 'not been noted', 'no shared notes', 'missing notes', "haven't noted"]):
        return (
            "SELECT tm.name, tm.role, MAX(sn.created_at) as last_note "
            "FROM team_members tm LEFT JOIN shared_notes sn ON sn.member_id = tm.id "
            "WHERE tm.active = 1 GROUP BY tm.id "
            "HAVING last_note IS NULL OR last_note < date('now', '-30 days') "
            "ORDER BY last_note ASC"
        )

    # OPEN FOLLOW-UPS
    if any(w in q for w in ['follow up', 'follow-up', 'unresolved', 'needs follow', 'waiting on']):
        return (
            "SELECT tm.name, tt.title, tt.created_at "
            "FROM team_tasks tt JOIN team_members tm ON tt.member_id = tm.id "
            "WHERE tt.status = 'open' AND tm.active = 1 AND tt.created_at < date('now', '-14 days') "
            "ORDER BY tt.created_at ASC"
        )

    # RECENT MEETINGS
    if any(w in q for w in ['recent meeting', 'last meeting', 'met with', 'meeting notes', 'who did i meet']):
        return (
            "SELECT tm.name, mtg.date, mtg.summary "
            "FROM team_meetings mtg JOIN team_members tm ON mtg.member_id = tm.id "
            "ORDER BY mtg.date DESC LIMIT 10"
        )

    # TASKS BY LEADER
    if any(w in q for w in ['tasks for', 'what does', 'assignments for']):
        return (
            "SELECT tm.name, COUNT(tt.id) as open_tasks "
            "FROM team_tasks tt JOIN team_members tm ON tt.member_id = tm.id "
            "WHERE tt.status = 'open' AND tm.active = 1 "
            "GROUP BY tm.name ORDER BY open_tasks DESC"
        )

    return None


def run(question: str) -> str:
    question = question.strip()
    if not question:
        return "No question provided."

    sql = _pattern_match(question)
    if not sql:
        return _NO_MATCH

    try:
        uri = f"file:{WATSON_DB}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            cur = conn.execute(sql)
            rows = cur.fetchall()
            desc = cur.description
        if not rows:
            return "No results found."
        return _format_rows(rows, desc)
    except Exception as e:
        return f"Query error: {e}\n\nQuery:\n{sql}"
