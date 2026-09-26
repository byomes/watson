"""One-off CLI: tag Donna/Kaci/Lucie/Tara as staff, then DM all 6 target
people (jobs/congregation/pin_collection.TARGETS) asking them to pick
their own Deacon App PIN, and register a pending_reply ask for each so
bot.py's compute_team_chat_reply recognizes their next reply as an
attempt to answer it.

Usage:
  cd ~/watson && PYTHONPATH=. python3 jobs/congregation/collect_deacon_pins.py

Safe to re-run: leadership_roles insert is INSERT OR IGNORE, and
pending_reply.ask() is INSERT OR REPLACE (re-running just re-sends the DM
and resets that person's 48h window).
"""
import sqlite3

from jobs.congregation.pin_collection import CONGREGATION_DB, NEEDS_STAFF_ROLE, PROMPT_TEMPLATE, TARGETS
from jobs.people import pending_reply
from jobs.telegram.send_to_person import send_to_person


def _tag_staff_roles() -> None:
    conn = sqlite3.connect(CONGREGATION_DB)
    try:
        for name in NEEDS_STAFF_ROLE:
            member_id = TARGETS[name]["member_id"]
            conn.execute(
                "INSERT OR IGNORE INTO leadership_roles (member_id, role) VALUES (?, 'staff')",
                (member_id,),
            )
        conn.commit()
    finally:
        conn.close()
    print(f"Tagged staff role for: {', '.join(sorted(NEEDS_STAFF_ROLE))}")


def _send_pin_requests() -> None:
    for name, ids in TARGETS.items():
        first_name = name.split()[0]
        message = PROMPT_TEMPLATE.format(first_name=first_name)
        sent = send_to_person(ids["person_id"], message)
        if not sent:
            print(f"FAILED to message {name} (person_id={ids['person_id']}) -- not onboarded, skipped")
            continue
        pending_reply.ask(name, "pin_collection", {})
        print(f"Messaged {name}, awaiting PIN reply.")


def main() -> None:
    _tag_staff_roles()
    _send_pin_requests()


if __name__ == "__main__":
    main()
