"""leader_tool_usage_report.py -- print every onboarded (telegram_chat_id
set) team member / deacon, with their total uses and last-used timestamp
for Telegram-based leader tools (currently just team_chat). Leaders who've
never used anything still show up, with 0 uses / "never" -- that's the
point, to see who isn't using what Bill built for them.

Also backs the dashboard's "Leader Usage" More-page tile via
jobs/telegram/leader_tool_usage_api.py, which calls the same build_report().

Usage:
  PYTHONPATH=/home/billyomes/watson python3 jobs/telegram/leader_tool_usage_report.py
"""
from jobs.telegram.leader_tool_usage import build_report

if __name__ == "__main__":
    for r in build_report():
        print(f"{r['name']}: {r['uses']} uses, last used {r['last_used'] or 'never'}")
