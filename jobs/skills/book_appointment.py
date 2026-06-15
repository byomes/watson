import re
import json
from datetime import datetime, timedelta
from jobs.people.api import congregation_search, people_list
from jobs.telegram.pending import store_pending_action

def run(message: str) -> str:
    import requests

    # Step 1 — Parse natural language with Ollama
    prompt = f"""Extract appointment details from this message and return ONLY a JSON object with these exact keys:
title, date, time, duration_minutes, person_name, location.

Rules:
- date: ISO format YYYY-MM-DD. Today is {datetime.now().strftime('%Y-%m-%d')}. Handle relative dates like "tomorrow", "next Thursday".
- time: 24hr format HH:MM
- duration_minutes: integer, default 60 if not mentioned
- person_name: full name if mentioned, otherwise null
- location: string if mentioned, otherwise null
- title: if not explicitly stated, use 'Meeting with [person_name]' if a person is mentioned, otherwise use the most descriptive phrase from the message

Message: "{message}"

Return ONLY the JSON object, no other text."""

    try:
        resp = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": "qwen2.5-coder:7b", "prompt": prompt, "stream": False},
            timeout=30
        )
        raw = resp.json().get("response", "").strip()
        # Strip markdown fences if present
        raw = re.sub(r"```json|```", "", raw).strip()
        parsed = json.loads(raw)
    except Exception as e:
        return f"Could not parse appointment details: {e}"

    title = parsed.get("title") or "Appointment"
    date_str = parsed.get("date")
    time_str = parsed.get("time")
    duration = int(parsed.get("duration_minutes") or 60)
    person_name = parsed.get("person_name")
    location = parsed.get("location")

    if not date_str or not time_str:
        return "Could not determine date or time. Please include both — for example: 'book an appointment with Dave Henderson Thursday at 2pm'."

    # Step 2 — Build start/end datetimes
    try:
        start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duration)
    except Exception as e:
        return f"Could not parse date/time: {e}"

    # Step 3 — Create Google Calendar event
    try:
        from jobs.gcal.create_event import create_event
        event = create_event(
            title=title,
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            location=location or "",
            description=f"Booked by Watson"
        )
        event_link = event.get("htmlLink", "")
    except Exception as e:
        return f"Calendar event could not be created: {e}"

    confirm_lines = [
        f"✓ Appointment created: {title}",
        f"  {start_dt.strftime('%A, %B %-d at %-I:%M %p')} ({duration} min)",
    ]
    if location:
        confirm_lines.append(f"  Location: {location}")
    if event_link:
        confirm_lines.append(f"  {event_link}")

    # Step 4 — If person mentioned, look them up and draft confirmation email
    if person_name:
        matches = congregation_search(person_name)
        if not matches or isinstance(matches, dict):
            # Fallback: search by last name only
            last_name = person_name.strip().split()[-1]
            if last_name.lower() != person_name.lower():
                fallback = congregation_search(last_name)
                if fallback and not isinstance(fallback, dict):
                    matches = fallback
        if not matches or isinstance(matches, dict):
            # Try watson.db people table
            all_people = people_list() or []
            matches = [p for p in all_people if person_name.lower() in p.get("name", "").lower()]
            if not matches:
                last_name = person_name.strip().split()[-1]
                matches = [p for p in all_people if last_name.lower() in p.get("name", "").lower()]

        if matches:
            person = matches[0]
            email = person.get("email")
            name = person.get("name", person_name)

            if email:
                email_draft = f"""To: {email}
Subject: Appointment Confirmation — {title}

Hi {name.split()[0]},

This is a confirmation of your upcoming appointment with Dr. Bill Yomes.

Details:
  Date: {start_dt.strftime('%A, %B %-d, %Y')}
  Time: {start_dt.strftime('%-I:%M %p')}
  Duration: {duration} minutes
"""
                if location:
                    email_draft += f"  Location: {location}\n"

                email_draft += """
If you need to reschedule, please visit williamckyomes.com/meet.

Watson
AI-powered digital assistant
Office of Dr. Bill Yomes
williamckyomes.com/start"""

                confirm_lines.append(f"\nDraft confirmation ready for {name} ({email}):")
                confirm_lines.append(email_draft)
                confirm_lines.append("\nReply 'send' to send, 'edit: [changes]' to revise, or 'cancel' to discard.")

                # Store as pending action for reply-threading
                store_pending_action(
                    action_type="email_draft",
                    telegram_message_id=None,  # set after send
                    payload=json.dumps({
                        "to": email,
                        "subject": f"Appointment Confirmation — {title}",
                        "body": email_draft,
                        "person_name": name
                    })
                )
            else:
                confirm_lines.append(f"\nFound {name} in contacts but no email on file.")
        else:
            confirm_lines.append(f"\nNo contact found matching '{person_name}'. Reply with their email address to send a confirmation.")

    return "\n".join(confirm_lines)
