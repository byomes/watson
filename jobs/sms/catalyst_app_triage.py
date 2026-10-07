"""jobs/sms/catalyst_app_triage.py -- reads new rows from
data/catalyst_app_inbox.db (filled by jobs/sms/catalyst_app_inbox.py) and
Telegrams Dr. Bill ONLY when a Catalyst app message looks like it needs a
pastoral response. Ordinary conversation (logistics, scheduling, thanks,
links, jokes) is marked reviewed and never alerted.

Judgment: local Ollama (qwen2.5:14b, no paid LLM anywhere in this path).
A keyword net runs underneath as a high-recall backstop: if Ollama is down
or returns garbage, a keyword hit still alerts (labeled keyword-only), and
a row with no hit and no verdict stays unreviewed so the next run retries.

Alert text is a factual relay (group, sender line, the message as written,
a one-line reason). Watson authors no pastoral or relational wording
(Guardrail 3). Bill-only Telegram, per the standing quiet-hours rule Bill
himself is exempt.

Usage: python -m jobs.sms.catalyst_app_triage [--dry-run]
"""
import logging
import os
import re
import sqlite3
import sys

import requests

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.ollama_json import generate_json
from jobs.sms.catalyst_app_inbox import DB, _db

log = logging.getLogger(__name__)
MODEL = "qwen2.5:14b"
OLLAMA_URL = "http://localhost:11434/api/generate"

_KEYWORDS = re.compile(
    r"\b(pray(er|ing)?|hospital|er\b|emergency|surgery|cancer|diagnos\w+|passed away|died|death|funeral|"
    r"grie(f|ving)|suicid\w+|depress\w+|anxiety|panic|abuse\w*|affair|divorce|separat\w+|lost (my|his|her) job|"
    r"laid off|can'?t pay|eviction|overdose|rehab|addict\w*|struggl\w+|hurting|need(s)? (to talk|help)|"
    r"call me|talk to (pastor|bill)|counsel\w*)\b",
    re.I,
)

_PROMPT = """You are screening church group-chat messages for Dr. Bill Yomes, the pastor of Catalyst Community Church.
Decide whether the message below likely NEEDS A PASTORAL RESPONSE from him: for example illness, hospitalization, surgery, a death or grief, a crisis, mental-health or addiction struggles, marriage or family trouble, financial hardship, a prayer request, spiritual doubt or struggle, someone hurt or leaving the church, a safety concern, or a direct request for the pastor's time or counsel.
It does NOT need one if it is ordinary conversation: scheduling, logistics, who is bringing what, thanks, jokes, links, photos, event details, or routine ministry coordination. Messages written by Dr. Bill Yomes himself do not need one.
When genuinely unsure whether a serious need is present, lean toward true.

Group: {group}
Notification title: {title}
Message: {text}

Reply with only JSON: {{"pastoral": true or false, "urgency": "urgent" or "soon" or "routine", "reason": "one short factual sentence"}}"""


def _ensure_cols(c: sqlite3.Connection) -> None:
    have = {r[1] for r in c.execute("PRAGMA table_info(catalyst_app_notifications)")}
    for col, typ in (("verdict", "TEXT"), ("reason", "TEXT"), ("alerted_at", "TEXT")):
        if col not in have:
            c.execute(f"ALTER TABLE catalyst_app_notifications ADD COLUMN {col} {typ}")


def _telegram(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
        return r.ok
    except Exception as exc:
        log.error("catalyst_app_triage: telegram send failed: %s", exc)
        return False


def classify(group: str, title: str, text: str) -> dict | None:
    """Returns {"pastoral": bool, "urgency": str, "reason": str} or None if the model failed."""
    try:
        out = generate_json(
            OLLAMA_URL, model=MODEL, prompt=_PROMPT.format(group=group or "(unknown)", title=title or "", text=text or ""),
            timeout=180, retries=1,
        )
        if isinstance(out, dict) and "pastoral" in out:
            return {"pastoral": bool(out["pastoral"]), "urgency": str(out.get("urgency", "soon")),
                    "reason": str(out.get("reason", ""))[:200]}
    except Exception as exc:
        log.warning("catalyst_app_triage: ollama failed: %s", exc)
    return None


def run(dry_run: bool = False) -> int:
    alerts = 0
    with _db() as c:
        _ensure_cols(c)
        rows = c.execute(
            "SELECT id, title, text, big_text, sub_text, conversation FROM catalyst_app_notifications "
            "WHERE verdict IS NULL ORDER BY posted_ms"
        ).fetchall()
        for rid, title, text, big, sub, convo in rows:
            body = big or text or ""
            group = convo or sub or title or ""
            if not body.strip():
                c.execute("UPDATE catalyst_app_notifications SET verdict='skip', reason='empty' WHERE id=?", (rid,))
                continue
            if re.match(r"\s*(Dr\.? Bill Yomes|Pastor Bill Yomes|Catalyst Community Church)\s*:", body):
                c.execute("UPDATE catalyst_app_notifications SET verdict='skip', reason='own message' WHERE id=?", (rid,))
                continue
            v = classify(group, title, body)
            kw = bool(_KEYWORDS.search(f"{title or ''} {body}"))
            if v is None and not kw:
                continue  # leave unreviewed, retry next run
            pastoral = v["pastoral"] if v else True
            reason = v["reason"] if v else "keyword match (model unavailable)"
            urgency = v["urgency"] if v else "soon"
            if not pastoral:
                c.execute("UPDATE catalyst_app_notifications SET verdict='normal', reason=? WHERE id=?", (reason, rid))
                continue
            msg = (f"Catalyst app message that may need a pastoral response ({urgency}).\n"
                   f"Group: {group}\nFrom/title: {title}\nMessage: {body[:600]}\nWhy flagged: {reason}")
            if dry_run:
                print(msg, "\n")
                continue
            if _telegram(msg):
                c.execute("UPDATE catalyst_app_notifications SET verdict='alerted', reason=?, alerted_at=datetime('now') WHERE id=?", (reason, rid))
                alerts += 1
    return alerts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = run(dry_run="--dry-run" in sys.argv)
    print(f"alerts sent: {n}")
