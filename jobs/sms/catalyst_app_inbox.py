"""jobs/sms/catalyst_app_inbox.py -- capture Catalyst302 app (Subsplash)
notifications from the gateway phone into data/catalyst_app_inbox.db.

Reads the Android notification list over USB adb (`dumpsys notification
--noredact`), so no extra app or listener permission is needed, and works
under the phone's Do Not Disturb (DND mutes sound/peek only; the posts
still land in the list). Poll about once a minute: a notification the
user/phone dismisses between polls is missed.

Push delivery needs the app NOT force-stopped (jobs/sms/catalyst_app_send.py
relaunches it when done). This module only RECORDS; it never replies or
sends anything. Whether/how Watson acts on rows is a separate decision.

Usage: python -m jobs.sms.catalyst_app_inbox        (one poll, prints new rows)
"""
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time

from jobs.sms.adb_client import ADB, connect_device

log = logging.getLogger(__name__)
PKG = "com.subsplashconsulting.s_7BVGB9"
DB = os.path.expanduser("~/watson/data/catalyst_app_inbox.db")

_REC_RE = re.compile(r"^\s{4}NotificationRecord\(")
_FIELDS = {
    "title": "android.title",
    "text": "android.text",
    "big_text": "android.bigText",
    "sub_text": "android.subText",
    "conversation": "android.conversationTitle",
}


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute(
        """CREATE TABLE IF NOT EXISTS catalyst_app_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL, posted_ms INTEGER NOT NULL,
            title TEXT, text TEXT, big_text TEXT, sub_text TEXT, conversation TEXT,
            captured_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(key, posted_ms))"""
    )
    return c


def _extra(block: str, name: str):
    m = re.search(r"^\s+" + re.escape(name) + r"=\w+ \((.*)\)\s*$", block, re.M)
    return m.group(1) if m else None


def parse(dump: str, pkg: str = PKG) -> list[dict]:
    """Splits the dump into NotificationRecord blocks and returns the ones for pkg."""
    blocks, cur = [], None
    for line in dump.splitlines():
        if _REC_RE.match(line):
            cur = [line]
            blocks.append(cur)
        elif cur is not None:
            if line.startswith("  ") and not line.startswith("      ") and line.strip():
                cur = None  # left the record list
            else:
                cur.append(line)
    out = []
    for b in blocks:
        text = "\n".join(b)
        if f"pkg={pkg} " not in b[0]:
            continue
        key = re.search(r"^\s+key=(\S+)", text, re.M)
        when = re.search(r"^\s+when=(\d+)", text, re.M)
        if not (key and when):
            continue
        row = {"key": key.group(1), "posted_ms": int(when.group(1))}
        for col, extra in _FIELDS.items():
            row[col] = _extra(text, extra)
        out.append(row)
    return out


def poll() -> list[dict]:
    serial = connect_device()
    if not serial:
        log.error("catalyst_app_inbox: no adb device")
        return []
    r = subprocess.run([ADB, "-s", serial, "shell", "dumpsys", "notification", "--noredact"],
                       capture_output=True, text=True, timeout=30)
    new = []
    with _db() as c:
        for row in parse(r.stdout):
            cur = c.execute(
                "INSERT OR IGNORE INTO catalyst_app_notifications "
                "(key, posted_ms, title, text, big_text, sub_text, conversation) VALUES (?,?,?,?,?,?,?)",
                (row["key"], row["posted_ms"], row["title"], row["text"], row["big_text"], row["sub_text"], row["conversation"]),
            )
            if cur.rowcount:
                new.append(row)
    return new


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for r in poll():
        print(time.strftime("%H:%M:%S", time.localtime(r["posted_ms"] / 1000)), r["title"], "|", r["text"])
