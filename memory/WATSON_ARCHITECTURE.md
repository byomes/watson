# Watson Architecture
*Single source of truth. Last updated: June 21, 2026.*
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
- **Ollama:** Bound to `0.0.0.0`. Models: `llama3.2:3b` (primary chat), `phi3:mini` (tasks), `qwen2.5-coder:7b` (code/reasoning), `gemma3:1b` (fast)

### FMSPC — Windows Desktop (GPU Tasks Only)

- **Specs:** RTX 3070 Ti (8GB VRAM), 128GB RAM
- **Role:** Whisper transcription, large-model Ollama inference
- **Whisper:** faster-whisper, Whisper Large-v3
- **Ollama:** `qwen2.5:14b`

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

**All web development happens on the Beelink.** Windows machine is no longer needed for web work. Claude Code builds on the Beelink, commits, pushes to GitHub, Vercel deploys automatically.

---

## Web Properties

| URL | Repo | Purpose |
|-----|------|---------|
| `williamckyomes.com` | `byomes/wcky` | William's personal site |
| `williamckyomes.com/room` | `byomes/wcky` | Writing Room — private partner community |
| `williamckyomes.com/twj/read` | `byomes/wcky` | TWJ manuscript reader — DO NOT change route |
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
| `watson.db` | `~/watson/data/watson.db` | Core system: tasks, reminders, people, chat, donors, appointments, blog drafts, facebook queue, connect cards, writing room |
| `congregation.db` | `~/watson/data/congregation.db` | Pastoral CRM: members, attendance, connect cards, next steps, prayer requests, follow-ups |
| `donors.db` | `~/watson/data/donors.db` | Givebutter donor and transaction records |
| ChromaDB | `~/watson/kb/chroma/` | KB vector index — 3,795 chunks from 453 documents |

### watson.db Key Tables

- `tasks`, `reminders`, `people`, `chat_sessions`, `tg_pending_actions`
- `blog_drafts`, `facebook_queue`, `reading_list`
- `connect_cards`, `donors`, `appointments`
- `writing_room_partners`, `writing_room_posts`, `writing_room_beta_feedback`
- `writing_room_messages`, `writing_room_calls`, `writing_room_reset_tokens`

---

## Active Services (Beelink systemd)

| Service | Purpose |
|---------|---------|
| `watson-bot.service` | Telegram bot |
| `watson-dashboard.service` | Flask dashboard, port 5200 |

Deploy pattern: `git pull && sudo systemctl restart [service]`

---

## LLM Stack

| Layer | Tool | Use |
|-------|------|-----|
| Claude.ai | This interface | Strategy, architecture, spec, writing |
| Claude Code | `--dangerously-skip-permissions` on Beelink | File editing, building, committing |
| `llama3.2:3b` | Beelink Ollama | Primary Watson chat and intent classification |
| `qwen2.5-coder:7b` | Beelink Ollama | Structured tasks, KB, code reasoning |
| `phi3:mini` | Beelink Ollama | Background tasks |
| `gemma3:1b` | Beelink Ollama | Fast/lightweight queries |
| `qwen2.5:14b` | FMSPC Ollama | Heavy inference |

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
| Subsplash | Connect cards | Forwards to `watson.wcky@gmail.com` label |
| Tailscale Funnel | Public Watson API access | `https://watson.tail0243ff.ts.net` → port 5200 |
| Vercel | Web hosting | Auto-deploy on push to `main` for all web repos |
| Upstash KV | Legacy data store | Blog draft queue, TWJ reader credentials (Writing Room now uses watson.db) |
| Bible API | Scripture lookup | `api.scripture.api.bible` — NIV, CSB, NASB |
| Serper.dev | Web search | Used in KB and research jobs |

---

## Jobs Architecture

> ⚠️ Every cron entry must include `PYTHONPATH=/home/billyomes/watson` inline.
> Venv python: `/home/billyomes/watson/venv/bin/python`

### Active Scheduled Jobs

| Job | Schedule | Purpose |
|-----|----------|---------|
| `jobs/scheduler.py` | Daily 10am | Publish blog drafts from `watson.db` |
| `jobs/ingest_drafts.py` | Every 15 min | Poll Upstash KV → `watson.db` |
| `core/pipeline.py` | Daily 6am | Main content pipeline |
| `jobs/facebook/facebook_post.py` | Every 15 min | Facebook post queue |
| `jobs/email_job/draft_email.py` | Thu 7am | Weekly email draft |
| `jobs/connect_cards/intake.py` | Every 30 min | Parse Subsplash connect cards from Gmail |
| `jobs/connect_cards/email_reports.py --bill` | Mon 5am | Prayer + follow-ups → Dr. Bill |
| `jobs/connect_cards/email_reports.py --donna --kaci` | Tue 5am | Attendance → Donna + Kaci |
| `jobs/connect_cards/email_reports.py --sync` | Sun 4am | Silent attendance sync |
| `jobs/connect_cards/attendance_intake.py` | Every 30 min | Attendance intake |
| `jobs/connect_cards/correction_handler.py` | Every 30 min | Attendance corrections |
| `jobs/connect_cards/missed_report.py` | Mon 6am | Missed report |
| `jobs/connect_cards/shepherding_report.py` | Wed 6am | Pastoral care digest |
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

### Other Jobs (Available)

- `jobs/bible.py` — Bible lookup (NIV, CSB, NASB)
- `jobs/gcal/` — Google Calendar availability, booking, clear_day
- `jobs/writing_room/` — onboard.py, reset.py, api.py (Flask blueprint)
- `jobs/kb/` — KB search, build, ingest
- `jobs/dev/` — Claude Code agent launcher

---

## Writing Room (`williamckyomes.com/room`)

Private community hub for Writing Room Partners (ARC readers).

**Architecture:** Next.js pages on wcky site → Watson API → `watson.db`

**Watson API base:** `https://watson.tail0243ff.ts.net` (Tailscale Funnel)

**Auth:** `X-Watson-Key` header shared secret (`WRITING_ROOM_API_KEY` in Watson `.env` and Vercel)

**Partner flow:** Apply → Watson alerts William via Telegram → Approve/Deny buttons → credentials generated → welcome email → Kit tag `writing-room-partner`

**Sections:** Board (community posts), Beta (draft feedback), Prayer (prayer wall), Write (direct message to William), Calls (upcoming video calls)

**Admin:** `/room/admin` — William's read-only view. Auth via `WRITING_ROOM_ADMIN_USER` / `WRITING_ROOM_ADMIN_PASS` in Vercel env.

**Watson job files:**
- `jobs/writing_room/__init__.py` — shared helpers
- `jobs/writing_room/monitor.py` — polls tables, fires Telegram alerts
- `jobs/writing_room/onboard.py` — approval flow, credentials, welcome email, Kit tag
- `jobs/writing_room/reset.py` — password reset token flow
- `jobs/writing_room/remind.py` — video call reminders to all partners
- `jobs/writing_room/api.py` — Flask blueprint, 10 routes, registered on dashboard app

**Beta content:** `~/wcky/src/content/books/twj/beta/` — separate from `/twj/read` manuscript files

---

## WCKY Site Key Routes

| Route | Notes |
|-------|-------|
| `/twj/read` | **DO NOT CHANGE** — reader bookmarks depend on this route |
| `/twj/press` | TWJ press kit |
| `/room` | Writing Room application form (public) |
| `/room/login` | Partner login |
| `/room/admin` | William's admin view |
| `/room/reset` | Password reset |
| `/room/(protected)/*` | Board, Beta, Prayer, Write, Calls — requires session |
| `/draft` | Blog draft submission |
| `/meet` | Public booking page |
| `/dashboard` | Redirect to Watson dashboard |

**Manuscript files:** `~/wcky/src/content/books/twj/chapters/`
**Blog posts:** `~/wcky/content/blog/YYYY-MM-DD-slug.md`

---

## TWJ Reader (`/twj/read`)

- Username/password session via httpOnly cookie
- 14 sections: introduction, 12 chapters, conclusion
- Copy protection: `user-select: none`, no right-click, Ctrl+C blocked
- Per-chapter feedback → Upstash KV
- Credentials managed via `watson-admin.vercel.app`

---

## The Wrong Jesus (Book)

- **Status:** Manuscript complete. 14 sections live at `/twj/read`.
- **Press kit:** `williamckyomes.com/twj/press`
- **Co-editor:** Mel Yomes
- **Beta reader system:** Fully operational via watson-admin + Writing Room `/room/beta`
- **Next:** TWJ provisioning job — bulk credentials + Kit emails to ARC readers at launch

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

## Content Pipeline

- Sermon audio → Whisper (FMSPC) → cleanup → article → social seeds
- Articles publish Tue/Thu/Sat 10am to `williamckyomes.com/blog`
- Facebook format: `[title]\n\n[excerpt — 2 sentences max]\n\n[url]\n\n#Apologetics #Theology #Faith`
- Weekly email draft generated Thursdays → Kit delivery
- Blog draft submission: `williamckyomes.com/draft` → Upstash KV → `watson.db` → `scheduler.py`

---

## Personal Knowledge Base

- **Location:** `~/watson/kb/documents/` — 453 documents
- **Contents:** Sermon transcripts, Bible study notes, handouts
- **Vector index:** ChromaDB at `~/watson/kb/chroma/` — 3,795 chunks
- **Transcription pipeline output:** `~/watson/kb/transcripts/`
- **Transcription backlog:** 10 years of sermon audio on FMSPC — not yet processed
- **KB search:** Planned — `jobs/kb_search.py` — Telegram query → results

---

## Telegram Bot

- Service: `watson-bot.service`
- Primary away interface: commands, alerts, briefing, Bible lookup, task management, Writing Room approvals
- **Intent routing order in `bot.py`:**
  1. Pending action check (reply threading via `tg_pending_actions`)
  2. Explicit command pre-checks (`_SKILL_PRE_CHECKS`) — 18 unambiguous triggers
  3. Skill router (explicit skill slugs)
  4. Ollama intent classifier (`llama3.2:3b`)
  5. General Ollama chat fallback

---

## Watson Dashboard

- **Port:** 5200 / **Service:** `watson-dashboard.service`
- **URL (home):** `http://192.168.1.204:5200`
- **URL (Tailscale):** `http://100.117.237.96:5200`
- **URL (public):** `https://watson.tail0243ff.ts.net`
- **Nav tabs:** Home, Briefing, Tasks, Reminders, Reading, More
- **Saved as iPhone PWA** — remove and re-add to Home Screen after safe area CSS changes

---

## Watson Identity & Email

- **Gmail:** `watson.wcky@gmail.com`
- **SMTP alias:** `watson@williamckyomes.com` (sends via Gmail SMTP)
- **From line:** `Watson <watson@williamckyomes.com>` or `FMS Team <watson@faithmakessense.com>`
- **Signature:** Watson / AI-powered digital assistant / Office of Dr. Bill Yomes

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
- **Reauth:** `jobs/gcal/reauth.py`

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
- **Upstash KV:** Requires `json.loads()` double-unwrap in `_kv_get`.
- **`/twj/read`:** Never change this route. Reader bookmarks depend on it.
- **Windows git:** `git pull` can fail with `mmap failed` — use `git fetch origin` then `git reset --hard origin/main`.
- **PowerShell:** No `&&` chaining. No `grep` — use `Get-ChildItem | Select-String`.

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

---

## Open Items (June 21, 2026)

### Active Bugs

1. `ingest_drafts.py` cron — verify PYTHONPATH set correctly on Beelink
2. `kb/transcripts` gitignored — transcript archiving sends broken Telegram URL
3. `/draft` page UI copy — still says "Pushing to GitHub…" — update to "Queuing…"
4. Facebook post excerpt — needs 2 sentences max + hashtags
5. Dashboard briefing tab — not fully functional, needs debugging
6. Donna not receiving Tuesday attendance report — check `--donna` flag or Tuesday cron
7. `memory/skills.json` — references `jobs.calendar.clear_day` (non-existent) — should be `jobs.gcal.clear_day`
8. GitHub push token (`watson-all`) — may need renewal

### Pending Confirmation

- Subsplash forwarding connect cards to `watson.wcky@gmail.com`
- Post-meeting pastoral notes fix — end-to-end confirmation pending
- `archive_transcripts.py` cron — not yet added to Beelink crontab

### Planned / Not Yet Built

1. Morning briefing auto-push — no manual Telegram command
2. FMS site rebuild — `~/fms`, Next.js, all data through Watson
3. KB search job — `jobs/kb_search.py`
4. TWJ provisioning job — bulk credentials + Kit emails to ARC readers at launch
5. Transcription backlog — 10 years of sermon audio on FMSPC
6. Weekly email end-to-end test
7. Book development job — `jobs/book/research_brief.py`
8. `/menu` Telegram command
9. Watson self-improvement system — architecture approved, build deferred
10. Adelphos Academy Watson integration
11. Catchall email — `watson@williamckyomes.com` + `watson@faithmakessense.com`
12. Email button on daily briefing — article links → Watson drafts Kit email
13. Writing Room — end-to-end test (Writing Room just launched June 21, 2026)

### Retired / Decided Against

- ~~Sub-agents (Charles, Jenny, Mark)~~ — Watson runs jobs, no agent personas
- ~~Write interface (`write.wcky.com`)~~ — Bill writes in Google Docs
- ~~MiniMax M3 local deployment~~ — requires data center hardware
- ~~Open WebUI~~ — replaced by Watson dashboard
- ~~`/meet` public booking page~~ — using Reclaim
- ~~watson-ui as primary interface~~ — deprioritized
- ~~Gemini in coding loop~~ — caused file corruption, permanently removed
- ~~Windows machine for web development~~ — all web dev now on Beelink
