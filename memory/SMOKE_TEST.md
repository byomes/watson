# Watson Smoke Test
*Run: 2026-06-21 19:21 EDT*

---

## Summary

- **185 Python files** across `jobs/` and `bot/` — all pass syntax check
- **5 core/ files** — all pass syntax check
- **All active cron jobs** pass both syntax and import checks
- **3 issues fixed automatically** (see below)
- **3 warnings** requiring manual review

---

## Passing (185 files — all jobs/ and bot/)

All Python files under `jobs/` and `bot/` pass `py_compile` syntax check.
All active cron job entry points pass deep import check (importlib exec_module).

**Cron jobs verified:**
- `jobs/scheduler.py` — syntax + import OK
- `jobs/ingest_drafts.py` — syntax + import OK
- `core/pipeline.py` — syntax + import OK
- `jobs/facebook/facebook_post.py` — syntax + import OK
- `jobs/connect_cards/intake.py` — syntax + import OK
- `jobs/connect_cards/email_reports.py` — syntax + import OK
- `jobs/connect_cards/attendance_intake.py` — syntax + import OK
- `jobs/connect_cards/correction_handler.py` — syntax + import OK
- `jobs/connect_cards/missed_report.py` — syntax + import OK
- `jobs/connect_cards/shepherding_report.py` — syntax + import OK
- `jobs/email_intake.py` — syntax + import OK
- `jobs/email_reply/reader.py` — syntax + import OK
- `jobs/email_job/draft_email.py` — syntax + import OK
- `jobs/pastoral_notes/prompt.py` — syntax + import OK
- `jobs/pastoral_notes/reminder.py` — syntax + import OK
- `jobs/reminders/daily_summary.py` — syntax + import OK
- `jobs/reminders/check_timed.py` — syntax + import OK
- `jobs/gcal/token_health.py` — syntax + import OK
- `jobs/gcal/pre_meeting_brief.py` — syntax + import OK
- `jobs/givebutter/sync.py` — syntax + import OK
- `jobs/givebutter/notify.py` — syntax + import OK
- `jobs/writing_room/monitor.py` — syntax + import OK
- `jobs/writing_room/remind.py` — syntax + import OK
- `jobs/dev/file_map.py` — syntax + import OK
- `jobs/dev/update_arch.py` — syntax + import OK
- `jobs/skillbuilder/audit.py` — syntax + import OK
- `bot/bot.py` — syntax + import OK

---

## Fixed Automatically (3 fixes)

### 1. bot/bot.py — Dead gemini_coder imports removed
`bot.py` had four blocks that imported `jobs.dev.gemini_coder` (a module that doesn't exist — Gemini was retired). These were inside runtime conditional branches, not at module level, so the bot loaded fine. But sending `build:`, `debug:`, `apply N`, or `cancel N` to the Telegram bot would have caused an `ImportError` crash.

**Removed blocks:**
- `build:` / `watson build:` → `request_build` (lines 557–565)
- `debug:` / `watson debug:` → `request_debug` (lines 567–575)
- `apply N` → `apply_build` (lines 591–601)
- `cancel N` → `cancel_build` (lines 602–608)

`debug:` now routes correctly to the `claude_debug` skill via the skill router.

### 2. memory/skills.json — Dead module references fixed
Two skill entries pointed to modules that don't exist:

| Skill | Old module | Fix |
|-------|-----------|-----|
| `gemini_fetch` | `jobs.research.gemini_fetch` | Status → `disabled` |
| `clear_day` | `jobs.calendar.clear_day` (ghost dir, retired) | Module → `jobs.gcal.clear_day`, status → `disabled` |

`clear_day` is set to `disabled` because `jobs/gcal/clear_day.py` is not yet built. The module path is now correct for when it is built.

### 3. Missing __init__.py files — Created
Seven directories used as Python packages were missing `__init__.py`. Python 3.12 namespace packages prevent these from causing import failures, but explicit `__init__.py` files are correct practice and required for `-m` style invocations.

Created empty `__init__.py` in:
- `jobs/` (needed for `-m jobs.gcal.pre_meeting_brief` etc.)
- `jobs/tasks/` (skills.json: `jobs.tasks.add_task`)
- `jobs/facebook/` (bot.py: `from jobs.facebook.facebook_post import ...`)
- `jobs/telegram/` (bot.py: `from jobs.telegram.pending import ...`)
- `jobs/kb/` (contains `archive_transcripts.py`)
- `jobs/dadjoke/` (skills.json: `jobs.dadjoke.joke`)
- `jobs/misc/` (skills.json: `jobs.misc.riddle`)

---

## Warnings (3 — manual review needed)

### W1. .env has duplicate SMTP variable blocks
`~/watson/.env` contains three sets of `WATSON_SMTP_*` variables (lines 54–71). The last definition wins, which is the correct Gmail credentials. The earlier blocks (a startlogic.com entry with blank credentials, and a duplicate gmail block) are dead weight. Not a runtime error but should be cleaned up.

**Action:** Edit `.env` to remove lines 54–67, keeping only the final Gmail SMTP block.

### W2. jobs/briefing/ is a ghost directory
`jobs/briefing/` has no `.py` source files, only a `__pycache__/` with orphan compiled files (`gemini_narrative.cpython-312.pyc`, `gemini_relevance.cpython-312.pyc`). These are compiled artifacts from deleted modules. No runtime impact, but the directory is confusing.

**Action:** `rm -rf ~/watson/jobs/briefing` (or at minimum `rm -rf ~/watson/jobs/briefing/__pycache__`)

### W3. skills disabled but not yet built
Two skills are now correctly marked `disabled` but need to be built:
- `clear_day` — `jobs/gcal/clear_day.py` — block/push appointments (was listed as Open Item in architecture)
- `gemini_fetch` — `jobs/research/gemini_fetch.py` — if still wanted, must be rebuilt using a non-Gemini fetch approach

---

## Crontab Jobs

| Schedule | Job | Status |
|----------|-----|--------|
| `0 10 * * *` | `jobs/scheduler.py` | ✅ |
| `*/15 * * * *` | `jobs/ingest_drafts.py` | ✅ |
| `0 6 * * *` | `core/pipeline.py` | ✅ |
| `*/15 * * * *` | `jobs/facebook/facebook_post.py` | ✅ |
| `*/30 * * * *` | `jobs/connect_cards/intake.py` | ✅ |
| `0 5 * * 1` | `jobs/connect_cards/email_reports.py --bill` | ✅ |
| `0 5 * * 2` | `jobs/connect_cards/email_reports.py --donna --kaci` | ✅ |
| `0 4 * * 0` | `jobs/connect_cards/email_reports.py --sync` | ✅ |
| `*/30 * * * *` | `jobs/connect_cards/attendance_intake.py` | ✅ |
| `*/30 * * * *` | `jobs/connect_cards/correction_handler.py` | ✅ |
| `0 6 * * 1` | `jobs/connect_cards/missed_report.py` | ✅ |
| `0 6 * * 3` | `-m jobs.connect_cards.shepherding_report` | ✅ |
| `* * * * *` | `jobs/email_intake.py` | ✅ |
| `*/15 * * * *` | `jobs/email_reply/reader.py` | ✅ |
| `0 7 * * 4` | `jobs/email_job/draft_email.py` | ✅ |
| `*/15 * * * *` | `jobs/pastoral_notes/prompt.py` | ✅ |
| `*/15 * * * *` | `jobs/pastoral_notes/reminder.py` | ✅ |
| `0 10,13:30,17 * * 1,2,3,4,6` | `jobs/reminders/daily_summary.py` | ✅ |
| `*/5 * * * *` | `jobs/reminders/check_timed.py` | ✅ |
| `0 7 * * *` | `jobs/gcal/token_health.py` | ✅ |
| `*/5 * * * *` | `-m jobs.gcal.pre_meeting_brief` | ✅ |
| `0 6 * * *` | `-m jobs.givebutter.sync` | ✅ |
| `15 6 * * *` | `-m jobs.givebutter.notify` | ✅ |
| `0 7 * * 1` | `-m jobs.skillbuilder.audit` | ✅ |
| `*/5 * * * *` | `jobs/writing_room/monitor.py` | ✅ |
| `*/15 * * * *` | `jobs/writing_room/remind.py` | ✅ |
| `0 2 * * *` | `jobs/dev/file_map.py` | ✅ |
| `0 2 * * *` | `jobs/dev/update_arch.py` | ✅ |

**All 28 cron jobs pass. Zero failures.**

---

## Other Notes

- `urllib3` version warning from `requests` package is cosmetic — not an error.
- `WRITING_ROOM_SESSION_SECRET` is missing from `.env` (architecture doc lists it as required). The Writing Room launched June 21, 2026 — verify this is set or sessions may not persist.
- `WATSON_API_URL` is missing from `.env`. Required by Writing Room wcky integration per architecture doc.
