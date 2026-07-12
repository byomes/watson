# Watson Architecture
*Single source of truth. Last updated: June 29, 2026.*
*Claude Code must read this file before any build.*

---

## What Is Watson

Watson is Dr. Bill Yomes's personal AI assistant system — not a single bot, but an orchestrated ecosystem of jobs, hardware, and interfaces. Watson does not use sub-agents. Watson runs **jobs**.

Watson acts on Dr. Bill's behalf under his supervision. Always identified openly as Watson. Never pastors, counsels, or speaks theologically without permission. Never guesses. Terse, efficient, direct.

**Non-negotiable constraints:**
- No hallucination. If Watson does not know, Watson says so and stops.
- Not an image bearer. No soul, no Holy Spirit access, no spiritual authority under any framing.
- Clarity over assumption. When clarification is needed, ask one specific question and wait.

---

## Hardware

### Beelink EQi12 — Primary Watson Server (LIVE)

- **Specs:** Intel i5 12th gen, 32GB DDR4, 500GB NVMe
- **OS:** Linux Mint XFCE
- **Hostname:** `watson` / **User:** `billyomes`
- **Local IP:** `192.168.1.204`
- **Tailscale IP:** `100.117.237.96`
- **Tailscale hostname:** `watson.tail0243ff.ts.net`
- **SSH:** `ssh billyomes@watson` or `ssh billyomes@192.168.1.204`
- **Tailscale Funnel:** `https://watson.tail0243ff.ts.net` → publicly reachable → proxies to `http://localhost:5200`
- **Dashboard:** Flask app, port 5200, `watson-dashboard.service`
- **Ollama:** Bound to `0.0.0.0`. Models: `llama3.2:3b` (primary chat/intent), `qwen2.5-coder:7b` (Dev Loop, KB, structured reasoning), `phi3:mini` (background tasks), `gemma3:1b` (fast/lightweight)

### FMSPC — Windows Desktop (GPU Tasks Only)

- **Specs:** RTX 3070 Ti (8GB VRAM), 128GB RAM
- **Role:** Whisper transcription, large-model Ollama inference
- **Whisper:** faster-whisper, Whisper Large-v3
- **Ollama:** `qwen2.5:14b` (accuracy-sensitive jobs: pastoral notes, email drafts, shepherding reports)
- **Not always-on.** FMSPC env vars left in `.env` for future use.

### PBLaptop — Windows Laptop

- Secondary machine. OneDrive synced. No Ollama.

### HP Stream — RETIRED

- Offline. Replaced by Beelink.

---

## Repos & Paths

| Repo | GitHub | Beelink path | Deploy |
|------|--------|--------------|--------|
| Watson | `github.com/byomes/watson` | `~/watson` | Manual pull + restart |
| WCKY site | `github.com/byomes/wcky` | `~/wcky` | Vercel auto on push |
| Watson Admin | `github.com/byomes/watson-admin` | `~/watson-admin` | Vercel auto on push |
| Watson UI | `github.com/byomes/watson-ui` | `~/watson-ui` | Vercel auto on push |
| FMS site | `github.com/byomes/fms` | `~/fms` (planned) | Vercel auto on push |

**All web development happens on the Beelink.** Claude Code builds on the Beelink, commits, pushes to GitHub, Vercel deploys automatically.

---

## Web Properties

| URL | Repo | Purpose |
|-----|------|---------|
| `williamckyomes.com` | `byomes/wcky` | William's personal site |
| `williamckyomes.com/thewrongjesus` | `byomes/wcky` | TWJ book launch page — countdown, Kit signup |
| `williamckyomes.com/arc` | `byomes/wcky` | ARC reader signup (open) |
| `williamckyomes.com/room` | `byomes/wcky` | Writing Room — private partner community (invitation-only) |
| `williamckyomes.com/twj/read` | `byomes/wcky` | **Retired** (2026-07-01, TWJ/ARC consolidation) — redirects to `/arc/dashboard` |
| `williamckyomes.com/twj/press` | `byomes/wcky` | TWJ press kit |
| `williamckyomes.com/meet` | `byomes/wcky` | Public booking page |
| `williamckyomes.com/dashboard` | `byomes/wcky` | Redirect → `https://watson.tail0243ff.ts.net` |
| `watson.tail0243ff.ts.net` | — | Watson dashboard (public via Funnel) |
| `watson-admin.vercel.app` | `byomes/watson-admin` | Book/reader management admin |
| `faithmakessense.com` | `byomes/fms` (planned) | FMS ministry site — rebuild pending |
| `adelphosacademy.com` | — | Moodle 5.0 theology school |

---

## Databases

| DB | Path | Contents |
|----|------|----------|
| `watson.db` | `~/watson/data/watson.db` | Core system: tasks, reminders, people, chat, donors, appointments, blog drafts, facebook queue, connect cards, writing room, login vault, dev loop projects |
| `congregation.db` | `~/watson/data/congregation.db` | Pastoral CRM: members, attendance, connect cards, next steps, prayer requests, follow-ups |
| `donors.db` | `~/watson/data/donors.db` | Givebutter donor and transaction records |
| ChromaDB | `~/watson/kb/chroma/` | KB vector index — 3,795 chunks from 453 documents |

### watson.db Key Tables

- `tasks`, `reminders`, `people`, `chat_sessions`, `tg_pending_actions`
- `blog_drafts`, `facebook_queue`, `reading_list`
- `connect_cards`, `donors`, `appointments`
- `writing_room_partners`, `writing_room_posts`, `writing_room_beta_feedback`
- `writing_room_messages`, `writing_room_calls`, `writing_room_reset_tokens`
- `arc_readers`, `arc_reader_commitments`, `arc_reader_feedback`, `arc_sessions` — ARC reader signup, commitment tracking, manuscript feedback, sessions
- `twj_readers`, `twj_feedback` — legacy TWJ reader accounts/feedback, migrated from Upstash KV; dashboard UI tab retired, data and routes intact
- `login_challenges` — login vault challenge/response pairs (dashboard-only)
- `dev_projects` — Dev Loop project tracking
- `memory_sessions` — persistent chat memory, injected into Ollama system prompt
- `routing_corrections` — intent correction log; memory note prepended after 5+ in 30 days
- `team_tasks`, `shared_notes`, `team_members` — leadership team management
- `church_events` — special events log, feeds State of the Church report
- `bug_tracker` — session bug log (open on discovery, resolved with commit_hash on fix); see Issues tab

### congregation.db Key Tables

- `members` — includes `member_status` (active/deceased/disconnected/non_local/snowbird), `campus_preference` (Wilmington/Online/Hybrid), `shepherding_exempt`
- `attendance`, `connect_cards`, `next_steps`, `prayer_requests`, `follow_ups`
- `member_conflicts` — Sunday 5pm Telegram conflict report with 3-button resolution

---

## Active Services (Beelink systemd)

| Service | Purpose |
|---------|---------|
| `watson-bot.service` | Telegram bot |
| `watson-dashboard.service` | Flask dashboard, port 5200 |

Deploy pattern: `cd ~/watson && git pull && sudo systemctl restart watson-bot.service watson-dashboard.service`

---

## LLM Stack

| Layer | Tool | Use |
|-------|------|-----|
| Claude.ai | This interface | Strategy, architecture, spec, writing |
| Claude Code | `--dangerously-skip-permissions` on Beelink | File editing, building, committing |
| `llama3.2:3b` | Beelink Ollama | Primary Watson chat, intent classification, session summarization |
| `qwen2.5-coder:7b` | Beelink Ollama | Dev Loop, KB search, structured reasoning |
| `phi3:mini` | Beelink Ollama | Background tasks |
| `gemma3:1b` | Beelink Ollama | Fast/lightweight queries |
| `qwen2.5:14b` | FMSPC Ollama | Accuracy-sensitive: pastoral notes, email drafts, shepherding, State of Church synthesis |

**No Claude API calls in automated Watson jobs.** Ollama handles all automated inference.

---

## Integrations

| Service | Purpose | Notes |
|---------|---------|-------|
| Google Calendar | Scheduling, booking, pre-meeting briefs | OAuth2, `~/watson/config/token.json`, scopes: Gmail + Calendar |
| Gmail IMAP | Connect cards, email intake, reply handler | `watson.wcky@gmail.com` |
| Gmail SMTP | Outbound email | `smtp.gmail.com:587`, sends as `watson@williamckyomes.com` alias |
| Telegram | Primary away interface | `@wckyWatsonbot`, `watson-bot.service` |
| Kit (v3 + v4) | Email platform | v3: `api_key` query param + `api_secret` in body; v4: `X-Kit-Api-Key` header |
| Givebutter | Donor sync | Polls transactions → `donors.db` → Kit tags → Gmail thank-you |
| Subsplash | Connect cards | Forwards to `watson.wcky@gmail.com` label via snappages.com |
| Tailscale Funnel | Public Watson API access | `https://watson.tail0243ff.ts.net` → port 5200 |
| Vercel | Web hosting | Auto-deploy on push to `main` for all web repos |
| Upstash KV | Legacy — TWJ reader credentials only | Writing Room and blog drafts now use watson.db |
| Bible API | Scripture lookup | `api.scripture.api.bible` — NIV, CSB, NASB |
| Serper.dev | Web search | Used in KB and research jobs |
| Scribbl | Meet transcripts | Chrome extension → auto-emails transcript to `watson.wcky@gmail.com` post-call |
| OneDrive | Nightly backup | rclone `Watson-Backup` remote, 3am cron — backs up data/, config/, kb/chroma/, kb/documents/, .env |

---

## Jobs Architecture

> ⚠️ Every cron entry must include `PYTHONPATH=/home/billyomes/watson` inline.
> Venv python: `/home/billyomes/watson/venv/bin/python`

### Active Scheduled Jobs (from live crontab)

| Job | Schedule | Purpose |
|-----|----------|---------|
| `jobs/scheduler.py` | Daily 10am | Publish blog drafts from `watson.db` |
| `core/pipeline.py` | Daily 6am | Main content pipeline |
| `jobs/facebook/facebook_post.py` | Every 15 min | Facebook post queue |
| `jobs/email_job/draft_email.py` | Thu 7am | Weekly email draft |
| `jobs/connect_cards/intake.py` | Every 30 min | Parse Subsplash connect cards from Gmail |
| `jobs/connect_cards/email_reports.py --bill` | Mon 5am | Prayer + follow-ups → Dr. Bill |
| `jobs/connect_cards/email_reports.py --donna --kaci` | Tue 5am | Attendance → Donna + Kaci |
| `jobs/connect_cards/email_reports.py --sync` | Sun 4am | Silent attendance sync |
| `jobs/connect_cards/attendance_intake.py` | Every 30 min | Attendance intake |
| `jobs/connect_cards/correction_handler.py` | Every 30 min | Attendance corrections |
| `jobs/connect_cards/campus_classifier.py` | Mon 5:45am | Classify member campus from 8-week connect card history |
| `jobs/connect_cards/missed_report.py` | Mon 6am | Missed report — 3 sections: Wilmington, Online, Hybrid |
| `jobs/connect_cards/shepherding_report.py` | Wed 6am | Pastoral care digest |
| `jobs/connect_cards/conflict_report.py` | Sun 5pm | Member conflict report with 3-button Telegram resolution |
| `jobs/connect_cards/state_of_church.py` | Thu 4pm | State of the Church HTML email |
| `jobs/email_intake.py` | Every min | Gmail polling + triage |
| `jobs/email_reply/reader.py` | Every 15 min | Email reply handler |
| `jobs/reminders/daily_summary.py` | 10am, 1:30pm, 5pm (Mon–Sat) | Daily reminders |
| `jobs/reminders/check_timed.py` | Every 5 min | Timed reminder checks |
| `jobs/gcal/token_health.py` | Daily 7am | Google OAuth token health check |
| `jobs/gcal/pre_meeting_brief.py` | Every 5 min | Pre-meeting brief (25–35 min before VA/IP events) |
| `jobs/pastoral_notes/prompt.py` | Every 15 min | Post-meeting pastoral note prompts |
| `jobs/pastoral_notes/reminder.py` | Every 15 min | Pastoral note reminders |
| `jobs/givebutter/sync.py` | Daily 6am | Donor sync |
| `jobs/givebutter/notify.py` | Daily 6:15am | Donor thank-you notifications |
| `jobs/writing_room/monitor.py` | Every 5 min | Writing Room activity alerts |
| `jobs/writing_room/remind.py` | Every 15 min | Writing Room call reminders |
| `jobs/skillbuilder/audit.py` | Mon 7am | Skill audit |
| `jobs/team/pre_meeting.py` | Every 5 min | Team pre-meeting brief |
| `jobs/team/reminders.py --overdue` | Mon–Thu 10am | Overdue task reminders |
| `jobs/team/reminders.py --unanswered` | Mon–Thu 10am | Unanswered comms reminders |
| `jobs/team/note_task_scan.py` | Tue/Wed/Thu 7am | Extract tasks from shared notes → Donna approval email |
| `jobs/backup.py` | Daily 3am | OneDrive backup via rclone |
| `jobs/dev_loop/cleanup.py` | Mon 4am | Purge Dev Loop projects older than 7 days |
| `jobs/dev/file_map.py` | Daily 2am | Auto-update FILE_MAP.md |
| `jobs/dev/update_arch.py` | Daily 2am | Auto-update WATSON_ARCHITECTURE.md |
| `jobs/kb/archive_transcripts.py` | Daily 2am | Archive transcripts >30 days to kb/documents/ |

### Other Jobs (Available)

- `jobs/bible.py` — Bible lookup (NIV, CSB, NASB)
- `jobs/gcal/` — Google Calendar availability, booking, clear_day, reauth
- `jobs/writing_room/` — onboard.py, reset.py, api.py (Flask blueprint)
- `jobs/kb/` — KB search, build, ingest, archive
- `jobs/dev/` — Claude Code agent launcher, smoke tests, file map, arch update
- `jobs/dev_loop/` — trigger.py, loop.py, deliver.py, cleanup.py
- `jobs/meet/summarize.py` — Meet transcript summarization via Scribbl → Gmail → Watson

---

## Congregation Management

### Member Status System (`member_status` column)
- `active` — normal reporting (default)
- `deceased` — permanent exclusion from all reports
- `disconnected` — excluded from reporting; auto-reinstated via Telegram if they attend again
- `non_local` — never flagged as missing, stays in system
- `snowbird` — not flagged as missing, optional return date for auto-reinstatement

All non-active statuses excluded from: missed report, shepherding report, State of the Church members-not-seen list.

### Campus Classification (`campus_preference` column)
Values: `Wilmington`, `Online`, `Hybrid`

**Classifier logic** (`jobs/connect_cards/campus_classifier.py`, runs Mon 5:45am):
1. Count Online vs Wilmington connect cards in last 56 days
2. Both ≥ 2 → Hybrid
3. Either ≥ 5 (not hybrid) → that campus
4. Middle zone → whichever is higher; Wilmington tiebreak
5. No cards in 8 weeks → Wilmington

Manual override available in dashboard Member Management panel.

### Missed Report Sections
Three sections, each suppressed if empty: WILMINGTON CAMPUS / ONLINE CAMPUS / HYBRID CAMPUS

### Conflict Resolution
Sunday 5pm Telegram report with 3-button resolution: Keep Old / Keep New / Skip
Dashboard trigger available: "Run Conflict Check" in More tab.

---

## Dev Loop

- Runs locally on Beelink via `subprocess.Popen` (non-blocking)
- Script: `~/watson/jobs/dev_loop/loop.py`
- Trigger: `jobs/dev_loop/trigger.py` — called from Telegram `devloop:` command (no dashboard UI trigger since 2026-07-01)
- Ollama model: `qwen2.5-coder:7b` at `localhost:11434`
- Test method: syntax check only (`python3 -m py_compile`) — not execution
- Callback: `POST /api/dev-loop/callback` with `X-Watson-Key: WRITING_ROOM_API_KEY`
- Dashboard: no UI tab as of 2026-07-01 (removed from More menu, TWJ/ARC consolidation) — `/api/dev-loop/*` routes still live, reachable directly, just no dashboard entry point
- Logs: `~/watson/logs/devloop-{slug}.log`
- Cleanup: `jobs/dev_loop/cleanup.py` — Monday 4am, purges projects older than 7 days
- Projects staged to `~/watson/dev/<slug>/` — never auto-committed to main

---

## Writing Room (`williamckyomes.com/room`)

Private community hub for Writing Room Partners (invitation-only, earned via ARC completion).

**Architecture:** Next.js pages on wcky site → Watson API → `watson.db`
**Watson API base:** `https://watson.tail0243ff.ts.net` (Tailscale Funnel)
**Auth:** `X-Watson-Key` header shared secret (`WRITING_ROOM_API_KEY`)

**Partner funnel:** ARC signup (`/arc`) → complete 5 commitments → Writing Room invitation
**ARC commitments** (`src/app/arc/page.tsx`):
1. Pray for the book's impact
2. Read the book before the launch date
3. Post an honest review on Amazon on launch day
4. Share about the book on at least one social media platform
5. Tell people in your life who you think would connect with this book
**Kit tags:** `arc-reader`, `arc-complete`, `writing-room-partner`

**Sections:** Board (threaded posts), Beta (draft feedback), Read (ARC manuscript), Prayer (prayer wall), Write (direct message to William), Calls (upcoming video calls)
**Admin:** `/room/admin` — William's read-only view. Auth via `WRITING_ROOM_ADMIN_USER` / `WRITING_ROOM_ADMIN_PASS`.

**Watson job files:**
- `jobs/writing_room/monitor.py` — polls tables, fires Telegram alerts
- `jobs/writing_room/onboard.py` — approval flow, credentials, welcome email, Kit tag
- `jobs/writing_room/reset.py` — password reset token flow
- `jobs/writing_room/remind.py` — video call reminders to all partners
- `jobs/writing_room/api.py` — Flask blueprint, routes registered on dashboard app

---

## The Wrong Jesus (Book)

- **Status:** Manuscript complete. Reader retired from `/twj/read` (2026-07-01, TWJ/ARC consolidation) — now read via ARC manuscript reader at `/arc/dashboard`.
- **Manuscript time-lock** (`src/lib/launch-dates.ts`): unlocks 2026-07-15, closes 2026-09-15 (pinned to `TWJ_LAUNCH_DATE`). Admin-preview bypass (`is_admin_preview`) available to view outside the window.
- **Launch page:** `williamckyomes.com/thewrongjesus` — countdown timer, Kit signup
- **Launch page pending:** `KIT_API_KEY` + `KIT_TWJ_TAG_ID` Vercel env vars; `GIVEBUTTER_LINK`; `AMAZON_LINK`; flip `AMAZON_LIVE=true` at preorder
- **Press kit:** `williamckyomes.com/twj/press`
- **Co-editor:** Mel Yomes
- **Beta reader system:** Fully operational via watson-admin + Writing Room `/room/beta`
- **Next:** TWJ provisioning job — bulk credentials + Kit emails to ARC readers at launch

---

## Watson Dashboard

- **Port:** 5200 / **Service:** `watson-dashboard.service`
- **URL (local):** `http://192.168.1.204:5200`
- **URL (Tailscale):** `http://100.117.237.96:5200`
- **URL (public):** `https://watson.tail0243ff.ts.net`
- **Nav tabs:** Home, Notes, Tasks (in Team), Reminders, Reading, More
- **More menu sections (current):** Theme toggle, Briefing, Skills (command launcher), Reading List, Ministry, Events, Members, Publishing (Writing Room / ARC), Logins
- **More menu history:** "Reports" deleted 2026-06-27 (dead code removed, not merged elsewhere — `385b7fc`). "Church Events" renamed to "Events" (same feature, same `church_events` table/`/api/events`). Dev Loop section and Team Admin link removed from nav 2026-07-01, TWJ/ARC consolidation (`b5af9b1`) — UI-only cleanup, backend cron jobs/tables and `/admin` + `/api/dev-loop/*` routes untouched, just no dashboard entry point anymore.
- **Saved as iPhone PWA** — remove and re-add to Home Screen after safe area CSS changes
- **Dashboard interfaces:** `/` (Bill), `/admin` (Donna), `/team` (shared team view)

### Dashboard Routing
- SSE streaming via `/api/chat/stream`
- Skills execute immediately (no confirmation gate in dashboard)
- Telegram executes skills immediately (no gate)
- Session summaries stored in `memory_sessions`; injected into system prompt on next session
- 10 directive prefixes routed to `/api/terminal`: `cdb:`, `wdb:`, `web:`, `bible:`, `polish:`, `polish this:`, `kb:`, `build:`, `debug:`, `run:`

### Member Management (More tab)
- Full member list with search
- Per-member: status dropdown (Active/Deceased/Disconnected/Non-local/Snowbird), campus dropdown (Wilmington/Online/Hybrid), notes, snowbird return date
- Campus change saved immediately via `PATCH /api/members/<id>`

---

## Telegram Bot

- Service: `watson-bot.service`
- Primary away interface
- **Intent routing order in `bot.py`:**
  1. Pending action check (reply threading via `tg_pending_actions`)
  2. Explicit command pre-checks (`_SKILL_PRE_CHECKS`) — 18 unambiguous triggers
  3. Skill router (explicit skill slugs)
  4. Ollama intent classifier (`llama3.2:3b`)
  5. General Ollama chat fallback

---

## Watson Identity & Email

- **Gmail:** `watson.wcky@gmail.com`
- **SMTP alias:** `watson@williamckyomes.com` (sends via Gmail SMTP)
- **From line:** `Watson <watson@williamckyomes.com>` or `FMS Team <watson@faithmakessense.com>`
- **Signature:** Watson / AI-powered digital assistant / Office of Dr. Bill Yomes

---

## Persistent Memory System

Four-layer memory injection via `jobs/memory/prompt_builder.py`:
1. **Layer 1** — Hardcoded identity block (always present)
2. **Layer 2** — Recent `memory_sessions` scored by word overlap, junk-filtered, threshold of 2
3. **Layer 3** — Project-specific markdown files (`memory/projects/`)
4. **Layer 4** — ChromaDB archive, retrieve-on-demand only

Project memory files: `congregation.md`, `dev_loop.md`, `twj.md`, `writing_room.md`
Wired into both `bot.py` and `jobs/dev_loop/loop.py`.

---

## Content Pipeline

- Sermon audio → Whisper (FMSPC) → cleanup → article → social seeds
- Articles publish Tue/Thu/Sat 10am to `williamckyomes.com/blog`
- Facebook format: `[title]\n\n[excerpt — 2 sentences max]\n\n[url]\n\n#Apologetics #Theology #Faith`
- Weekly email draft generated Thursdays → Kit delivery
- Blog draft submission: `williamckyomes.com/draft` → `POST /api/submit-draft` → `watson.db` → `scheduler.py`
- `ingest_drafts.py` retired — Upstash KV no longer in blog pipeline

---

## Personal Knowledge Base

- **Location:** `~/watson/kb/documents/` — 453 documents
- **Contents:** Sermon transcripts, Bible study notes, handouts
- **Vector index:** ChromaDB at `~/watson/kb/chroma/` — 3,795 chunks, collection `sermons`
- **Transcription pipeline output:** `~/watson/kb/transcripts/`
- **Transcription backlog:** 10 years of sermon audio on FMSPC — not yet processed
- **Archive job:** `jobs/kb/archive_transcripts.py` — moves files >30 days from `kb/transcripts/` to `kb/documents/`, daily 2am

---

## Scheduling Rules

- Watson schedules Wed/Thu only for external bookings
- Mon: connect cards + sermon study
- Tue: elder 8am, staff 9am — Watson observes only
- Fri: Sabbath. Sat: family. Sun AM: church. Sun PM: light creative pipeline.
- Deep work: Wed/Thu 9am–2pm, 90-min blocks, 15-min breaks
- People always beat tasks. Tier 1 tasks immovable.
- Booking windows: Wed 10am–1pm; Thu 10am–1pm and 7–8:30pm; Sat 8–9:30am (pastoral only)

---

## Google Calendar

- **Auth:** OAuth2 — `~/watson/config/credentials.json` + `token.json`
- **Calendar ID:** `bill.yomes@gmail.com`
- **Scopes:** Gmail + Calendar
- **Token health check:** Daily 7am (`jobs/gcal/token_health.py`)
- **Reauth:** `/gcal-auth` web route in dashboard app

---

## FMS Site (Planned Rebuild)

- **Repo:** `github.com/byomes/fms` (not yet created)
- **Beelink path:** `~/fms` (not yet cloned)
- **Stack:** Next.js App Router, Tailwind — same pattern as wcky
- **Data:** All API/DB needs route through Watson (no Upstash)
- **Design:** Intellectual/academic — deep navy or charcoal, serif headlines
- **Status:** Rebuild planned. Build not started.

---

## Adelphos Academy

- **URL:** `adelphosacademy.com`
- **Platform:** Moodle 5.0
- **Moodle REST API:** Confirmed enabled
- **Planned Watson jobs:** Lesson builder, quiz generator, course spec system, weekly monitoring digest, student stuck alert, course announcement emails, student welcome message
- **Status:** In build queue — not yet started

---

## Credentials

- **Master store:** `SECRETS.md` on OneDrive — canonical for all API keys
- **Watson runtime:** `~/watson/.env`
- **Never commit credentials to GitHub**

### Key `.env` Variables

```
WRITING_ROOM_API_KEY=
WRITING_ROOM_ADMIN_USER=
WRITING_ROOM_ADMIN_PASS=
WRITING_ROOM_SESSION_SECRET=
WRITING_ROOM_EMAIL_FROM=Watson <watson@williamckyomes.com>
WATSON_API_URL=https://watson.tail0243ff.ts.net
```

---

## Development Conventions

- **Design:** Claude.ai (this interface)
- **Build:** Claude Code on Beelink (`--dangerously-skip-permissions`)
- **Deploy:** Claude Code commits + pushes → Vercel auto-deploys / Bill manually pulls Watson
- **Claude Code never SSHes.** Bill always pulls and restarts services manually.
- **Sudo access:** Claude Code has exactly one passwordless sudo permission — restarting
  watson-dashboard.service and watson-bot.service (via /etc/sudoers.d/watson-restart).
  No other sudo command is permitted under any circumstances.
- **sed vs Claude Code:** ≤3 steps use sed. 4+ steps go to Claude Code.
- **PYTHONPATH:** Always inline in cron — `PYTHONPATH=/home/billyomes/watson` — do not rely on standalone crontab variable.
- **Ghost directory danger:** Never name job directories after Python stdlib modules. `jobs/calendar/` → renamed `jobs/gcal/`. `jobs/email/` → renamed `jobs/email_job/`.
- **Ollama async:** All Ollama calls in bot must use `asyncio.to_thread()`. Never bare `requests.post()` in async context.
- **Kit API:** v3 and v4 require separate credentials. v3 tag creation: nested `{"tag": {"name": "..."}}`. v3 auth: `api_key` query param for GET, `api_secret` in POST body. v4: `X-Kit-Api-Key` header.
- **httpx pin:** Must stay at `0.25.2` for `python-telegram-bot 20.7` compatibility.
- **`/twj/read`:** Retired 2026-07-01 (TWJ/ARC consolidation) — now redirects to `/arc/dashboard`. Manuscript reading lives under ARC (`ManuscriptReader.tsx`).
- **Two-database architecture:** `congregation.db` for all pastoral/church data; `watson.db` for Watson system data. Jobs must target the correct DB.
- **Direct Python over Ollama for structured DB queries:** `cdb:` pattern matcher is more reliable than LLM SQL generation. Ollama falls through/times out on structured queries.
- **`_SKILL_PRE_CHECKS` and `skills.json` are independent:** Both must be updated when adding/changing skills.
- **New skill builds:** always append to `commands.json`; Watson maintains that file.
- **Windows git:** `git pull` can fail with `mmap failed` — use `git fetch origin` then `git reset --hard origin/main`.
- **PowerShell:** No `&&` chaining. No `grep` — use `Get-ChildItem | Select-String`.
- **Gemini permanently removed from coding loop** — caused file corruption.

---

## File Paths Quick Reference

| Location | Path |
|----------|------|
| Master credentials | `SECRETS.md` on OneDrive |
| Watson `.env` | `~/watson/.env` |
| Watson jobs | `~/watson/jobs/` |
| Watson data | `~/watson/data/` |
| Watson logs | `~/watson/logs/` |
| Watson memory | `~/watson/memory/` |
| Watson KB documents | `~/watson/kb/documents/` |
| Watson KB transcripts | `~/watson/kb/transcripts/` |
| Google OAuth credentials | `~/watson/config/credentials.json` |
| Google OAuth token | `~/watson/config/token.json` |
| WCKY site | `~/wcky/` |
| WCKY blog posts | `~/wcky/content/blog/` |
| TWJ chapters | `~/wcky/src/content/books/twj/chapters/` |
| TWJ beta drafts | `~/wcky/src/content/books/twj/beta/` |
| Watson Admin site | `~/watson-admin/` |
| Watson UI site | `~/watson-ui/` |
| FMS site (planned) | `~/fms/` |
| Sermon audio (FMSPC) | `E:\0 - Sermon Audio\incoming` |
| Dev Loop projects | `~/watson/dev/<slug>/` |
| Commands launcher | `~/watson/memory/commands.json` |

---

## Active Bugs

Tracked in the `bug_tracker` table (`watson.db`) — see the dashboard Issues tab, not this file. Open on discovery, resolved with a `commit_hash` once the fix is actually committed.

### Known limitation — KB search excerpt trimming (added 2026-07-08)

The 500-char excerpt window in `kb_search.py` centers on literal query-term proximity (first substring match of a query word in the chunk), not semantic relevance. Synthesis quality will degrade on queries where the underlying concept is present in a chunk but the exact search term doesn't appear near it in the source text. Not currently a fix target — noted for awareness only.

---

## Planned / Not Yet Built

1. Morning briefing auto-push — no manual Telegram command needed
2. Auto-generate social captions on blog publish — hook to `scheduler.py`, Telegram approval
3. Weekly email draft pipeline — briefing email button → article links → Watson drafts Kit email → approval
4. Follow-up reminders — "Follow up with Dave in 3 days" → Watson schedules nudge
5. Context-triggered reminders — "Remind me about X when I talk to Y" → fires on calendar match
6. `/menu` Telegram command — exists, needs review
7. People Registry — add a person via Telegram (lookup already built)
8. FMS site rebuild — `~/fms`, Next.js, all data through Watson
9. ARC welcome email — Watson detects new Kit signup, sends welcome automatically
10. ARC weekly digest — feedback summary → Telegram or email to Bill
11. TWJ provisioning job — bulk credentials + Kit emails to ARC readers at launch
12. Catchall email — `watson@williamckyomes.com` + `watson@faithmakessense.com`
13. Book development job — `jobs/book/research_brief.py`
14. Transcription backlog — 10 years of sermon audio on FMSPC
15. Weekly email end-to-end test
16. Adelphos Academy Watson integration (8 planned jobs)
17. GitHub token renewal — `watson-all` may need renewal; update SECRETS.md + `.env`
18. Watson self-improvement system — architecture approved, build deferred

---

## Retired / Decided Against

- ~~Sub-agents (Charles, Jenny, Mark)~~ — Watson runs jobs, no agent personas
- ~~Write interface (`write.wcky.com`)~~ — Bill writes in Google Docs
- ~~MiniMax M3 local deployment~~ — requires data center hardware
- ~~Open WebUI~~ — replaced by Watson dashboard
- ~~watson-ui as primary interface~~ — deprioritized
- ~~Gemini in coding loop~~ — caused file corruption, permanently removed
- ~~Windows machine for web development~~ — all web dev now on Beelink
- ~~Upstash KV in blog pipeline~~ — `ingest_drafts.py` retired, direct POST to Flask
- ~~FMSPC SSH for Dev Loop~~ — moved to local Beelink execution
- ~~iOS keyboard patch in dashboard chat~~ — attempted and reverted 7 times, permanently removed from build queue
- ~~Build Pipeline (`jobs/dev/build_pipeline.py`)~~ — Claude API spec/review/approve flow triggered by bare `build <request>` / `approve` in Telegram; last ran 2026-06-15, superseded by Dev Loop. Bot triggers removed 2026-07-03. File left in place, unreferenced.

---

## Recent Changes — 2026-06-29

### ~/watson
- 5b2a72f docs: file map 2026-06-29
- 9a142ed docs: architecture update 2026-06-29
- fea0062 feat: cdb_query — add 7 new pattern blocks, eliminate Ollama fallthrough for common queries
- c87850f feat: wire all 10 directive prefixes to terminal endpoint; replace gemini_coder with Dev Loop trigger
- fdfabf5 feat: Skills tab auto-fires self-contained commands, loads input-required commands into chat
- acaa598 fix: Skills tab commands route to /api/terminal instead of chat
- 727d0f1 fix: conflict_check fires async from dashboard — no timeout
- 5f3b606 fix: Brenda Boling name correction + conflict merge direction buttons (Keep Old/Keep New/Skip)

### Today (June 29, session)
- fix: missed_report.py — added member_status filter (excludes deceased/disconnected/non_local/snowbird)
- feat: campus_classifier.py — new job, Mon 5:45am, classifies campus from 8-week connect card history
- fix: missed_report.py — three campus sections (Wilmington/Online/Hybrid), each suppressed if empty
- feat: dashboard member management — campus dropdown added to member expand panel
- fix: app.py PATCH /api/members — campus_preference added to allowed fields

---

## Recent Changes — 2026-06-30

### ~/watson
- 6c4f10d Add Christmas sermon notes for book project
- 871fbae Add FBC conference transcripts nights 1-4
- 06a96b2 transcript: add 2026-06-29-FBC-Night4-JesusTheOnlyWay
- 0ace47d transcript: add 2026-06-29-FBC-Night3-JesusAndMiracles
- 0deaf10 transcript: add 2026-06-29-FBC-Night2-JesusGodIncarnate
- c0c0c64 transcript: add 2026-06-29-FBC-Night1-ThePromisedMessiah
- 474629c feat: add /api/kit/subscribe route
- 9200976 transcript: add 2026-06-29-Joshua-Ch4-Legacy-Before-Victory
- b22778e fix: sync_attendance — join members table, retire --sync cron
- 8d1d53c docs: architecture and build list updated June 29, 2026
- 5b2a72f docs: file map 2026-06-29
- 9a142ed docs: architecture update 2026-06-29

---

## Recent Changes — 2026-07-01

### ~/watson
- 3268c6b feat: Writing Room password management — admin reset + self-serve change
- 159eff8 fix: Writing Room invite email says five things, not six
- 47ef65e fix: reword 5th ARC commitment to 'tell people in your life...', fix stale 6-commitment email copy
- 084ff14 fix: evidence_required check uses {3,4,5} not stale {4,5,6}
- 11d14c3 feat: ARC manuscript reader on dashboard, feedback table/route; fix evidence_required {3,4,5}
- 443a497 feat: drop 'receive a copy' ARC commitment, now 5 commitments (auto-check removed)
- aba8b63 fix: ARC email senders use correct WATSON_GMAIL env vars + load_dotenv
- 01b3945 feat: Kit subscribe API, team task title editing, weekly completed report job
- cc75240 feat: ARC reader auth, commitment tracking, admin approval routes, Writing Room promotion flow
- d24d2ad feat: campus_classifier.py, KB reorg into subfolders, missed_report campus sections, dashboard/skills updates
- fe78489 feat: ARC reader signup endpoint — arc_readers table, Kit tag application, Telegram notification
- 3c62229 docs: file map 2026-06-30
- 697be3f docs: architecture update 2026-06-30

### ~/wcky
- db4eab7 feat: Writing Room self-serve password change
- 8232dbb feat: add Back to Commitments shortcut to ARC manuscript TOC
- aaca4c4 feat: add light/dark theme toggle to ARC manuscript reader
- 39267be fix: correct chapter counter labeling and TOC scroll offset in ARC manuscript reader
- 0de3284 fix: increase top padding below sticky bar from pt-12 to pt-16
- cc5c1ec fix: sticky bar offsets for global Header; commitment tracker now renders above manuscript
- a0c74b6 fix: sticky TOC bar now full-width and properly pinned outside reading column
- 5d6070f feat: replace chapter nav with full-width sticky bar + dropdown TOC, progress indicator
- 152a65f fix: ARC signup checkbox says fulfill all five, not six
- af87e83 fix: reword 5th ARC commitment on public signup page
- 96a4e37 fix: update commitment count copy from six to five
- 03bc8d9 fix: remove 'receive advance copy' from public ARC commitments list
- 8921f3a feat: ARC dashboard shows manuscript reader above commitment tracker
- acfc7c9 feat: ArcDashboard reflects 5 commitments, remove auto-check special case
- d9a30eb feat: add wcky favicon and apple-touch-icon
- 9fa36c1 fix: public nav 'Room' link replaced with 'ARC' pointing to /arc
- 6ae8e20 Merge branch 'main' of https://github.com/byomes/wcky
- 5e51159 feat: ARC reader login + commitment tracker dashboard with auto-save
- 2cc50fc publish: You Are Not What You Do
- 179a4c9 Merge branch 'main' of https://github.com/byomes/wcky
- f4c4703 fix: restore public ARC signup, wire to Watson-backed API instead of direct Kit form

### ~/watson-admin
- ba945b1 feat: Writing Room partner password reset
- c161ad2 feat: ARC Commitments admin review tab — approve/reject, suspicious flagging, Writing Room invite

---

## Recent Changes — 2026-07-02

### ~/watson
- 2b0fb14 docs: file map 2026-07-02
- a33e4b5 feat: add is_admin_preview bypass for ARC manuscript time lock
- 7f5ec82 fix: dynamic commitment total in arc_dashboard, was hardcoded to 6
- ab2c8cc feat: delete-credentials action for ARC/Writing Room, preserves posts/feedback
- 385b7df feat: word-based password generation for ARC/Writing Room (EFF wordlist, 3 words)
- 112791f docs: file map 2026-07-02
- 9777084 docs: architecture update 2026-07-02
- 6af90c0 feat: ARC password reset, resend welcome, and revoke — parity with Writing Room
- b5af9b1 fix: remove TWJ Readers, Dev Loop, and Team Admin from dashboard nav
- 8ca2cd1 docs: document Claude Code's scoped sudo restart permission
- bdd1da1 feat: Publishing dashboard UI — Writing Room / ARC / TWJ Readers tabs
- 219b2e1 refactor: extract ARC invite-to-writing-room into reusable function
- 531f667 feat: Writing Room resend-welcome action
- 7cee92e feat: TWJ Reader Watson API — admin CRUD + reader-facing login/session/feedback
- 6c112aa feat: TWJ reader KV→watson.db migration (twj_readers/twj_feedback tables)
- c9e6b36 chore: gitignore docs/briefing.html (auto-generated daily)
- 889baf5 chore: stop tracking auto-generated briefing.html
- ba2c524 chore: gitignore generated briefing.html at correct path
- 53173bc chore: stop tracking auto-generated briefing.html
- 9aa6af9 fix: sync --nav-h CSS var on load and resize
- c6d60a1 feat: auto-detect new calendar meeting-title prefixes, Telegram approval flow
- 87212ff fix: mobile toggle pill splits evenly into two equal tap targets
- 0346ee4 fix: mobile header wrapping and table scroll containment on Team Admin page
- 57200de feat: two-way Dashboard/Team toggle — header points to /admin, admin.html gets matching toggle back to /
- 36795c0 feat: 30-day persistent admin session, no re-login on every dashboard open
- e383ea0 feat: add Elders task category to Home dashboard tabs
- 1a7fbd1 fix: no-cache headers on /api/members and /api/members/search to prevent stale WebView cache
- f17fd31 feat: shepherding: directive prefix, Skills button now matches emailed report
- dcca945 feat: editable Last Seen date on member panel, inserts attendance row
- 49bd70a fix: critical care section requires 3+ visits (cards+attendance) before flagging
- c712a54 docs: file map 2026-07-01
- 5b381f9 docs: architecture update 2026-07-01

### ~/wcky
- a9b05fa feat: move TWJ section above recent posts on homepage
- 645d9cc refactor: consolidate lead magnet modal into single shared instance
- 3e7cbdd feat: add small-print ARC link below launch page email signup
- 5833366 fix: homepage TWJ section links to launch page instead of ARC signup
- 4f5d0ff fix: open Givebutter monthly partner link in new tab
- ff36972 fix: correct FMS monthly partner Givebutter link
- 69c27fe fix: apply Kit tag via correct v4 tags endpoint on TWJ signup
- 43bbaef content: update TWJ manuscript chapters (07-02-2026)
- bd45613 copy: rework /thewrongjesus description block
- 1e52dcb feat: admin preview bypass for manuscript time lock
- 18fe657 feat: time-lock ARC manuscript access, unlock 7/15 close 9/15
- befeeda publish: When Culture Knocks at the Door
- 6d5e35e copy: rename commitment tracker link text to ARC Login
- 4cefb94 copy: rename Commitment Tracker to ARC Login on login page
- bcf4e15 fix: /arc/dashboard shows error instead of redirecting on expired session
- 8ddc9dc fix: retire /twj/read, redirect to /arc/dashboard
- 92f5920 feat: repoint TWJ reader login/session/feedback at Watson instead of Upstash KV
- 071ce53 chore: gitignore tsconfig.tsbuildinfo

---

## Recent Changes — 2026-07-03

### ~/watson
- 4ee0ed7 docs: weekly architecture close-out — retire /twj/read, correct dashboard nav, ARC commitments, dedupe recent-changes
- 90c4066 docs: architecture update 2026-07-02
- 2b0fb14 docs: file map 2026-07-02
- a33e4b5 feat: add is_admin_preview bypass for ARC manuscript time lock
- 7f5ec82 fix: dynamic commitment total in arc_dashboard, was hardcoded to 6
- ab2c8cc feat: delete-credentials action for ARC/Writing Room, preserves posts/feedback
- 385b7df feat: word-based password generation for ARC/Writing Room (EFF wordlist, 3 words)
- 112791f docs: file map 2026-07-02
- 9777084 docs: architecture update 2026-07-02

### ~/wcky
- 6e4b72d fix: clarify Faith Makes Sense as Dr. Bill's teaching ministry in donor copy
- 9cb24ed feat: add dedicated OG/Twitter share images for remaining public pages
- bcb7816 feat: add dedicated OG/Twitter share image for /about
- f081f3c feat: add custom OG/Twitter share image for the site/homepage
- 2724f6c feat: add custom OG/Twitter share image for TWJ launch page
- 896eda8 fix: replace remaining em dashes and align footer link label
- af391c0 fix: replace em dash with comma in Monthly Partnership copy
- b67dfc6 fix: change Launch Team CTA button to filled gold style
- d50f3cb fix: align styling consistency across TWJ launch page sections
- df6eac9 feat: reposition ARC section as Launch Team CTA on TWJ launch page
- a9b05fa feat: move TWJ section above recent posts on homepage
- 645d9cc refactor: consolidate lead magnet modal into single shared instance
- 3e7cbdd feat: add small-print ARC link below launch page email signup
- 5833366 fix: homepage TWJ section links to launch page instead of ARC signup
- 4f5d0ff fix: open Givebutter monthly partner link in new tab
- ff36972 fix: correct FMS monthly partner Givebutter link
- 69c27fe fix: apply Kit tag via correct v4 tags endpoint on TWJ signup
- 43bbaef content: update TWJ manuscript chapters (07-02-2026)
- bd45613 copy: rework /thewrongjesus description block
- 1e52dcb feat: admin preview bypass for manuscript time lock
- 18fe657 feat: time-lock ARC manuscript access, unlock 7/15 close 9/15
- befeeda publish: When Culture Knocks at the Door
- 6d5e35e copy: rename commitment tracker link text to ARC Login
- 4cefb94 copy: rename Commitment Tracker to ARC Login on login page

---

## Recent Changes — 2026-07-04

### ~/watson
- ed55f6e fix: remove dead Build Pipeline triggers from bot.py, document retirement in architecture doc
- 2e9488c docs: rewrite CLAUDE.md — Beelink, current systems, required-first-step session instructions
- 8fb7dfa docs: file map 2026-07-03
- 6e585d7 docs: architecture update 2026-07-03

---

## Recent Changes — 2026-07-05

### ~/watson
- feat: `thesis_snapshots` id=2 inserted — second all-time snapshot (May 20–Jul 5 2026), 52 downloads/18 views/20 countries, pulled by hand from Digital Commons dashboard. Via throwaway script, not committed.
- data correction: `thesis_countries` snapshot_id=2 was initially entered with only 19 of 20 countries (South Africa omitted). Added South Africa (1 download) after confirming it still shows on the live dashboard — country sum now matches `total_downloads` (52) exactly, no gap. `raw_json` note on the snapshot corrected to match (previously described the gap as an "unattributable download," which was wrong — it was just the missing country row). Direct `sqlite3` data fix via throwaway script, no code changes.
- data correction: `thesis_countries` snapshot_id=1 (all-time backfill, May 20–Jul 4 2026) was originally entered with only 5 of 20 countries. Replaced with full 20-country list; corrected `thesis_snapshots.total_countries` from 19→20 (was miscounted at backfill time). `total_downloads` (51) unchanged — United States corrected from 24→23 to match. Direct `sqlite3` data fix, no script file, no code changes.
- 03e0c1f chore: remove dead app.py.bak, close stale dashboard button bug in arch doc
- fa9e261 feat: Thesis Tracker section in dashboard More tab
- df83d8e fix: thesis_snapshots window_type column, backfill first all-time seed row
- b10075e feat: thesis_tracker scaffolding — token health check + scraper schema
- 52370c0 docs: file map 2026-07-04
- f0f7da4 docs: architecture update 2026-07-04

### ~/wcky
- 987a726 copy: drop WCKY branding and add ellipsis pause on TWJ launch page
- 11ecb4e copy: drop WCKY branding from monthly-donor future-book copy
- 74f1f0a fix: replace em dashes in page metadata and copy across src/app

---

## Recent Changes — 2026-07-06

### ~/watson
- 118bd2e feat: vacation mode toggle — gate Telegram sends across ~50 call sites
- c99d3d7 docs: note thesis_snapshots id=2 insert and South Africa data correction
- 82c0c50 docs: note thesis_countries snapshot_id=1 data correction
- 1312e8e feat: Thesis Tracker Countries shows all-time history, drop Referrers from view
- 231443b docs: file map 2026-07-05
- 36afc8c archive: move aged transcripts to kb/documents
- 95ad694 docs: architecture update 2026-07-05

### ~/wcky
- 999f955 fix: match SHARE section background to adjacent Launch Team section
- 1eb326f fix: use dedicated SMS share copy on TWJ launch page
- a2535fe feat: add Share via Text and Share on Social buttons to TWJ launch page

---

## Recent Changes — 2026-07-07

### ~/watson
- ff12422 feat: More tab 2-column button grid with shared expand area below
- edb51d9 feat: bodyrec Watson API blueprint — migrate off Supabase
- f8ea23d docs: file map 2026-07-06
- 3603783 docs: architecture update 2026-07-06

### ~/wcky
- edcb202 feat: use TWJ_Launch_2.PNG as the OG/Twitter image site-wide

---

## Recent Changes — 2026-07-08

### ~/watson
- 06d1101 Remove invalid all-time date range line from thesis tracker
- f82061d chore: drop thesis_token_health table, remove dead bootstrap_db()
- 2b1a8c0 chore: remove thesis_tracker/token_health.py, superseded by scrape.py
- 1e9da58 docs: note thesis_tracker scrape.py daily cron schedule
- fa295f2 feat: Thesis Tracker scraper + Pull New Data button
- 2926cb8 fix: default-hide revoked/deleted accounts in Writing Room + ARC admin lists
- 657cc74 docs: file map 2026-07-07
- c0d9b0f docs: architecture update 2026-07-07

---

## Recent Changes — 2026-07-08

### ~/watson
- bd2888f docs: file map 2026-07-08
- 90f6606 feat: gold choropleth scale + theme-aware map (dark/light tile + fill swap)
- 08e3dd7 feat: interactive world map for Thesis Tracker countries
- 6f40c5f docs: file map 2026-07-08
- dd97b48 docs: architecture update 2026-07-08
- 06d1101 Remove invalid all-time date range line from thesis tracker
- f82061d chore: drop thesis_token_health table, remove dead bootstrap_db()
- 2b1a8c0 chore: remove thesis_tracker/token_health.py, superseded by scrape.py
- 1e9da58 docs: note thesis_tracker scrape.py daily cron schedule
- fa295f2 feat: Thesis Tracker scraper + Pull New Data button
- 2926cb8 fix: default-hide revoked/deleted accounts in Writing Room + ARC admin lists

---

## Recent Changes — 2026-07-09

### ~/watson
- 4ba9e30 feat: bug_tracker table + Issues tab + bug: directive
- f00e78d fix: pin huggingface-hub>=1.5.0,<2.0 for sentence_transformers compat
- 9ccb9ef fix: KB search timeout — trim excerpts + swap to llama3.2:3b
- 35c03ca fix: chat fallback timeout — use llama3.2:3b instead of qwen2.5:14b
- 7acf3e3 docs: architecture update 2026-07-08
- bd2888f docs: file map 2026-07-08
- 90f6606 feat: gold choropleth scale + theme-aware map (dark/light tile + fill swap)
- 08e3dd7 feat: interactive world map for Thesis Tracker countries
- 6f40c5f docs: file map 2026-07-08
- dd97b48 docs: architecture update 2026-07-08

---

## Recent Changes — 2026-07-10

### ~/watson
- 7444974 fix: bible_lookup skill silently ignored input via _run_skill dispatch
- 1e52f34 fix: close remaining bare-blocking-call findings from the sweep
- 278de54 fix: intent classifier uses llama3.2:3b instead of qwen2.5:7b
- 9c0c3e7 fix: unwrap bare requests.post() calls to Ollama/Calendar API from async bot handlers
- a9c9a3a docs: file map 2026-07-09
- 94b2627 archive: move aged transcripts to kb/documents
- 5bb3b43 docs: architecture update 2026-07-09

---

## Recent Changes — 2026-07-11

### ~/watson
- 635ec10 docs: file map 2026-07-10
- f23659f docs: architecture update 2026-07-10

---

## Recent Changes — 2026-07-12

### ~/watson
- 21ff4c3 fix: gutenberg.py surfaces request failures instead of silent empty results
- 66ec361 fix: classics: crashes SSE stream when gutenberg collection missing
- 479cdec feat: wire gutenberg:/classics: directives into Dashboard chat
- 6461011 feat: Project Gutenberg research job — gutenberg: search + approval-gated ingest, classics: query
- 1ecb80c docs: file map 2026-07-11
- 9b66172 docs: architecture update 2026-07-11
