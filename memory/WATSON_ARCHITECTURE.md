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
| `williamckyomes.com/twj/read` | `byomes/wcky` | TWJ manuscript reader — **DO NOT change route** |
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
- `login_challenges` — login vault challenge/response pairs (dashboard-only)
- `dev_projects` — Dev Loop project tracking
- `memory_sessions` — persistent chat memory, injected into Ollama system prompt
- `routing_corrections` — intent correction log; memory note prepended after 5+ in 30 days
- `team_tasks`, `shared_notes`, `team_members` — leadership team management
- `church_events` — special events log, feeds State of the Church report

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
- Trigger: `jobs/dev_loop/trigger.py` — called from Telegram `devloop:` command or dashboard
- Ollama model: `qwen2.5-coder:7b` at `localhost:11434`
- Test method: syntax check only (`python3 -m py_compile`) — not execution
- Callback: `POST /api/dev-loop/callback` with `X-Watson-Key: WRITING_ROOM_API_KEY`
- Dashboard: Dev Loop tab in More menu — project list, status badges, code viewer, Keep Going/Stop buttons
- Logs: `~/watson/logs/devloop-{slug}.log`
- Cleanup: `jobs/dev_loop/cleanup.py` — Monday 4am, purges projects older than 7 days
- Projects staged to `~/watson/dev/<slug>/` — never auto-committed to main

---

## Writing Room (`williamckyomes.com/room`)

Private community hub for Writing Room Partners (invitation-only, earned via ARC completion).

**Architecture:** Next.js pages on wcky site → Watson API → `watson.db`
**Watson API base:** `https://watson.tail0243ff.ts.net` (Tailscale Funnel)
**Auth:** `X-Watson-Key` header shared secret (`WRITING_ROOM_API_KEY`)

**Partner funnel:** ARC signup (`/arc`) → complete 6 commitments → Writing Room invitation
**ARC commitments:** advance copy, read before launch, Amazon review on launch day, share on ≥1 social platform, pray for impact, spread the word
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

- **Status:** Manuscript complete. 14 sections live at `/twj/read`.
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
- **More menu sections:** Theme toggle, Briefing, Skills (command launcher), Reports, Members, Church Events, Login Vault, Dev Loop, Team Admin
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
- **sed vs Claude Code:** ≤3 steps use sed. 4+ steps go to Claude Code.
- **PYTHONPATH:** Always inline in cron — `PYTHONPATH=/home/billyomes/watson` — do not rely on standalone crontab variable.
- **Ghost directory danger:** Never name job directories after Python stdlib modules. `jobs/calendar/` → renamed `jobs/gcal/`. `jobs/email/` → renamed `jobs/email_job/`.
- **Ollama async:** All Ollama calls in bot must use `asyncio.to_thread()`. Never bare `requests.post()` in async context.
- **Kit API:** v3 and v4 require separate credentials. v3 tag creation: nested `{"tag": {"name": "..."}}`. v3 auth: `api_key` query param for GET, `api_secret` in POST body. v4: `X-Kit-Api-Key` header.
- **httpx pin:** Must stay at `0.25.2` for `python-telegram-bot 20.7` compatibility.
- **`/twj/read`:** Never change this route. Reader bookmarks depend on it.
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

## Active Bugs (June 29, 2026)

1. Telegram "View on Dashboard" Dev Loop link opens `/#devloop` tab but doesn't deep-link to specific project
2. "Send to Claude Code" button — legacy button in dashboard, not yet removed
3. KB search (`qwen2.5-coder:7b`) — timed out at 14 min during testing; root cause unresolved
4. `/draft` page UI copy — may still say "Pushing to GitHub…" — verify and update to "Queuing…"

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
