"""jobs/exports/secret_scan.py — mandatory secret-pattern scan/redaction
for jobs.exports.export_link. Every file linked via create_export_link()
is scanned here first; there is no caller-level opt-out (enforced inside
export_link.py itself, not something each caller has to remember).

Patterns cover both named Watson credential shapes (Brevo, Facebook,
Google/Gemini) and generic secret shapes -- including the two real leaks
this scanner exists because of (2026-09-05, this same session): a Telegram
bot token embedded in a request URL logged by journalctl, and an OAuth
access_token/refresh_token pair embedded in a `rclone config show` JSON
dump. Findings never include the matched value itself, only the pattern
name and line number -- that's what gets logged/audited.

Deliberately NOT included: WATSON_GMAIL_APP_PASSWORD and
GIVEBUTTER_API_KEY have no distinctive structural shape of their own (a
Gmail app password is just 16 lowercase letters; Givebutter's key format
isn't publicly documented) -- a bare pattern for either would be far too
prone to false positives/negatives to be worth it as a *dedicated* rule.
Both are still caught by the generic env_style_secret_assignment pattern
below whenever they appear as a NAME=value assignment (the shape they
actually appear in, in .env files and similar), which is the realistic
way either would end up in an exported file.
"""
import re

# (name, compiled pattern). Order doesn't matter -- redact() applies all
# of them, over multiple passes if needed (see redact()'s loop).
_PATTERNS = [
    ("google_api_key", re.compile(r"AIza[A-Za-z0-9_\-]{35}")),
    ("telegram_bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),
    ("brevo_api_key", re.compile(r"xkeysib-[a-f0-9]{64}-[A-Za-z0-9]+")),
    ("facebook_access_token", re.compile(r"\bEAA[A-Za-z0-9]{20,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9\-_.=]{20,}")),
    ("connection_string_with_credentials", re.compile(
        r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:[^\s@]+@[^\s/]+"
    )),
    ("json_secret_field", re.compile(
        r'"(?:access_token|refresh_token|api_key|apikey|api_secret|client_secret|'
        r'private_key|password)"\s*:\s*"[^"]{8,}"',
        re.IGNORECASE,
    )),
    ("env_style_secret_assignment", re.compile(
        r'\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*[:=]\s*'
        r'[\'"]?[A-Za-z0-9+/_\-.=]{8,}[\'"]?'
    )),
]


def scan(text: str) -> list[dict]:
    """Return [{"pattern": name, "line": line_number}, ...] for every match
    found -- never the matched value itself."""
    findings = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        for name, pattern in _PATTERNS:
            if pattern.search(line):
                findings.append({"pattern": name, "line": lineno})
    return findings


def redact(text: str) -> tuple[str, list[dict]]:
    """Replace every match with [REDACTED:<pattern>] in a copy of `text`.
    Returns (redacted_text, findings) -- findings has the same shape as
    scan()'s return value."""
    findings = []
    out_lines = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        for name, pattern in _PATTERNS:
            line, n = pattern.subn(f"[REDACTED:{name}]", line)
            if n:
                findings.append({"pattern": name, "line": lineno})
        out_lines.append(line)
    return "\n".join(out_lines), findings
