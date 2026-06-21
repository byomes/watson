"""jobs/writing_room — Writing Room Partner community hub."""
import os
import secrets
import smtplib
import sqlite3
import string
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

DB = Path.home() / "watson" / "data" / "watson.db"

_BOT_TOKEN = lambda: os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
_CHAT_ID   = lambda: os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn


def bootstrap_db() -> None:
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS writing_room_partners (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                name                 TEXT NOT NULL,
                email                TEXT NOT NULL UNIQUE,
                username             TEXT UNIQUE,
                password_hash        TEXT,
                status               TEXT DEFAULT 'pending',
                why_join             TEXT,
                faith_description    TEXT,
                agreed_to_participate INTEGER DEFAULT 0,
                joined_at            TEXT,
                last_active          TEXT,
                welcome_sent         INTEGER DEFAULT 0,
                created_at           TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS writing_room_posts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_id       INTEGER REFERENCES writing_room_partners(id),
                section          TEXT NOT NULL,
                parent_id        INTEGER,
                content          TEXT NOT NULL,
                flagged          INTEGER DEFAULT 0,
                watson_alerted   INTEGER DEFAULT 0,
                created_at       TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS writing_room_beta_feedback (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_id     INTEGER REFERENCES writing_room_partners(id),
                target_type    TEXT NOT NULL,
                target_slug    TEXT NOT NULL,
                reaction       TEXT,
                comment        TEXT,
                watson_alerted INTEGER DEFAULT 0,
                created_at     TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS writing_room_messages (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_id     INTEGER REFERENCES writing_room_partners(id),
                name           TEXT NOT NULL,
                email          TEXT NOT NULL,
                message        TEXT NOT NULL,
                watson_alerted INTEGER DEFAULT 0,
                created_at     TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS writing_room_calls (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                title            TEXT NOT NULL,
                scheduled_at     TEXT NOT NULL,
                meeting_url      TEXT,
                reminder_24h_sent INTEGER DEFAULT 0,
                reminder_1h_sent  INTEGER DEFAULT 0,
                created_at        TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS writing_room_reset_tokens (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_id INTEGER REFERENCES writing_room_partners(id),
                token      TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                used       INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        try:
            conn.execute(
                "ALTER TABLE writing_room_partners ADD COLUMN faith_description TEXT"
            )
            conn.commit()
        except Exception:
            pass  # column already exists


def send_telegram(text: str, reply_markup: dict | None = None) -> None:
    token = _BOT_TOKEN()
    chat_id = _CHAT_ID()
    if not (token and chat_id):
        return
    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_markup:
        import json
        payload["reply_markup"] = json.dumps(reply_markup)
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json=payload,
        timeout=10,
    )


def send_email(to: str | list[str], subject: str, body: str, bcc: list[str] | None = None) -> None:
    host     = os.getenv("WATSON_SMTP_HOST", "smtp.gmail.com")
    port     = int(os.getenv("WATSON_SMTP_PORT", "587"))
    user     = os.getenv("WATSON_SMTP_USER", "")
    password = os.getenv("WATSON_SMTP_PASS", "")
    from_hdr = os.getenv("WRITING_ROOM_EMAIL_FROM", f"Watson <{user}>")

    recipients = [to] if isinstance(to, str) else to
    if bcc:
        recipients = recipients + bcc

    body_html = body.replace("\n", "<br>")

    msg = MIMEMultipart("alternative")
    msg["To"]      = to if isinstance(to, str) else ", ".join(to)
    msg["From"]    = from_hdr
    msg["Subject"] = subject
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(f"<html><body>{body_html}</body></html>", "html"))

    with smtplib.SMTP(host, port) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(user, password)
        smtp.sendmail(user, recipients, msg.as_string())


def generate_username(first_name: str) -> str:
    slug = first_name.lower().split()[0]
    slug = "".join(c for c in slug if c.isalpha())
    num  = secrets.randbelow(900) + 100
    return f"{slug}{num}"


def generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))
