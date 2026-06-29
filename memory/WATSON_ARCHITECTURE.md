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

---

## Recent Changes — 2026-06-21

### ~/watson
- 46a6497 docs: file map snapshot 2026-06-21
- 43d0d9a feat: Writing Room copy updates, Dr. Bill rename, faith description field
- 673bd7c docs: master architecture file
- 623ba0c skill: jobs.skillbuilder.theology_apologetics_testing.py — generated by qwen2.5-coder:7b
- a780259 fix: extend pastoral notes expiry window to 24 hours
- 61f0de7 fix: pastoral_notes insert matches actual DB schema
- 9d588f4 fix: remove chrome installer, add .deb to gitignore
- dce8794 fix: remove ghost jobs/calendar/ directory
- fa5a1fc feat: kb export skill — zip matching KB files via Telegram
- a0f20bf feat: wire kb: prefix to ChromaDB ask engine
- a15e0b2 fix: build_kb indexes kb/documents instead of kb/transcripts
- 17fabe1 feat: transcript archive job
- 0b2bd13 feat: post-meeting action item extraction via Ollama
- 7fcea88 feat: extract tasks from pastoral notes via Ollama after meetings
- f41d7ea feat: add pre-meeting pastoral brief job for VA/IP appointments
- c9ed12e refactor: rewrite donor thank-you templates as FMS Team voice
- 313f966 fix: update donor thank-you From display name to FMS Team
- 00f5d17 fix: update donor thank-you From address to watson@faithmakessense.com
- 5054aea refactor: send donor thank-you via Gmail SMTP instead of Kit API
- 64e5068 refactor: send transactional email via Kit v4 /v4/emails
- 1007c5d debug: print Kit v4 broadcast response status and body
- dbac1a2 fix: separate Kit v3 and v4 credentials in bot.py
- 1ec9455 fix: use correct auth header for Kit v4 broadcasts API
- 79cb66c feat: add Edit in Kit path to donor thank-you flow
- 7de4930 fix: correct Kit v3 tag creation body format
- a9a7479 fix: correct Givebutter API field mappings in sync.py
- b94b998 refactor: move Givebutter approval handling into watson-bot.service
- 5028ba8 feat: add Givebutter + Kit donor management job

### ~/wcky
- c0787e2 feat: replace Arc with Room in nav
- 30ab28a feat: Writing Room copy updates, Dr. Bill rename, faith description field
- e309a5f feat: Writing Room — /room routes, auth, admin, partner board, beta, prayer, calls
- 379c81e publish: Covenant Before Conquest

---

## Recent Changes — 2026-06-22

### ~/watson
- 10090d9 fix: add Telegram notification to archive_transcripts, add cron entry
- ca4a839 feat: send confirmation email to Donna after attendance correction
- f6907f6 fix: allow partner_id=0 (admin) to post replies
- 93390f6 feat: DELETE /api/writing-room/post/<id> route
- d95e0d3 fix: smoke test — remove dead gemini_coder imports, fix skills.json module paths, add missing __init__.py files
- b8ab2cb fix: remove @_require_key from login route, remove debug prints, fix duplicate decorators
- 31bc76a fix: debug admin login + ensure .env loaded in Flask
- e873f98 feat: admin login bypass for Writing Room
- b6b447b fix: list_partners includes faith_description, agreed_to_participate, created_at
- f5b4677 feat: email verification flow, admin API routes (revoke, approve, deny, verify)
- 339e33b chore: untrack database files
- da89d08 chore: cleanup retired files, add file_map and update_arch jobs
- dee8aec docs: file map 2026-06-21
- d39ab6f docs: file map 2026-06-21
- 733ed34 docs: file map 2026-06-21
- c15e408 docs: architecture update 2026-06-21
- 46a6497 docs: file map snapshot 2026-06-21
- 43d0d9a feat: Writing Room copy updates, Dr. Bill rename, faith description field
- 673bd7c docs: master architecture file
- 623ba0c skill: jobs.skillbuilder.theology_apologetics_testing.py — generated by qwen2.5-coder:7b
- a780259 fix: extend pastoral notes expiry window to 24 hours

### ~/wcky
- 9c27cc1 fix: Writing Room light mode — Read tab and prose typography
- fd9ad6e feat: replace icon toggle with Dark/Light pill in Writing Room header
- 85a4d8f feat: Writing Room light/dark mode toggle
- d31b59b fix: reply submit errors + mobile zoom on input focus
- 0288515 feat: replace ✕ delete buttons with Trash2 icon from lucide-react
- b6a623a fix: Writing Room delete — add try/catch to watsonDelete, enlarge delete buttons
- 0e1c112 feat: delete post — author and admin can delete posts
- d3d7beb feat: Facebook-style threaded board and prayer wall, fix 401 on posts
- 86910bf fix: add X-Watson-Key header to all Watson API calls from wcky
- 3c6228d feat: ARC manuscript reader in Writing Room, Read nav button
- 00a496c feat: hide site footer in Writing Room, taller RoomNav buttons
- 0e4c5b8 fix: Writing Room layout accounts for site header height
- 6d162c4 feat: show/hide password toggle on Writing Room login
- cdd8be7 feat: /room is now login page, application moved to /room/apply
- 16f7c70 feat: admin credentials work as Writing Room login
- b41e560 feat: email verification flow for Writing Room partners
- 76805e7 chore: remove legacy posts/ dir and orphaned /read/[slug] route
- c0787e2 feat: replace Arc with Room in nav
- 30ab28a feat: Writing Room copy updates, Dr. Bill rename, faith description field
- e309a5f feat: Writing Room — /room routes, auth, admin, partner board, beta, prayer, calls
- 379c81e publish: Covenant Before Conquest

### ~/watson-admin
- cd107b2 feat: Writing Room admin — partners, applications, messages, calls (TWJ pattern)
- ae3c23f feat: Writing Room admin section — partners, applications, messages, calls

---

## Recent Changes — 2026-06-23

### ~/watson
- a28ce7a feat: location intake endpoint POST /api/location
- b0ee05a fix: kb search — lazy import to prevent blocking Flask startup
- a2ef406 fix: kb search — run in thread pool to unblock Flask main thread
- d334ea4 fix: kb search — local embedding model only, increase Ollama timeout
- f88e5a3 feat: kb: search skill — ChromaDB sermons collection + llama3.2:3b synopsis with email follow-up
- 990d59d fix: tighten polish prompt — copy edit only, no expansion
- e3c1eb1 feat: polish this: skill — Ollama prose polish in Dr. Bill's voice
- 31577a5 feat: swap confirmation gate to dashboard, restore Telegram direct execution, add image rendering
- e15e6c2 feat: confirmation gate + correction logging for Telegram skill execution
- 02ef564 fix: SMS 'text ' trigger misroutes polish messages to SMS handler
- 176d955 fix: tighten Watson identity to prevent Dr. Bill hallucination
- 67ea4a9 fix: back button navigation blocked by awaited summarize call
- bd8335d feat: persistent memory system — session summarization + injection
- 11d2f13 fix: rename chat overlay topbar label from Watson to Dashboard
- 0bbbbbb fix: pass Watson system prompt as system role message in all Ollama calls
- dc0f8f6 feat: redesign chat tab as full-screen overlay
- 74b04eb feat: add Chat tab to dashboard with SSE streaming textarea
- 9da085c fix: add flask-cors to dashboard for Tailscale Funnel CORS preflight
- 757b61b feat: add /api/submit-draft endpoint, retire ingest_drafts.py + cron
- 3df4651 docs: file map 2026-06-22
- 2d444d2 transcript: add 2026-06-22-Joshua---Ch3---Walking-Between-Miracles
- 6058bf3 transcript: add 2026-06-22-Joshua---Ch2---Faith-Over-Culture
- 510e5db docs: architecture update 2026-06-22

### ~/wcky
- 8a65204 fix: 50 character minimum on faith description field in Writing Room apply form
- 3685a99 fix: replace "Join the Launch Team" CTA text with "Join the Writing Room"
- d73ee71 fix: redirect /arc to /room, replace ARC language with Writing Room
- 77a069f feat: draft page posts to Watson directly, retire Upstash KV route

---

## Recent Changes — 2026-06-24

### ~/watson
- 9eeaa33 fix: message delete uses _db() not get_db()
- b60a009 feat: delete comms messages from detail panel
- 5740a34 fix: remove duplicate arrow icon from profile back button
- f08a65a fix: back button typo fixed, gold arrow icon added to profile panel
- e5b8a3b fix: export deleteTask in TeamApp public API
- 71c3b94 feat: completed tasks show delete button, auto-switch to done filter on check
- 797c6cf fix: remove subtitle from both headers, W and WATSON only
- 22b013d fix: team header matches dashboard, back arrow visible, nav icons+labels, drag reorder disabled
- 72b009e fix: back arrow safe area, date right-aligned, nav icons+labels, disable drag reorder
- 51af48c feat: add telegram link and date to team header, fix scroll offset
- b69da60 feat: unified task view with Mine/Team filters
- e39a7ba feat: remove tasks tab from dashboard, tasks live in team
- ee5827d fix: task intake writes to team_tasks for Bill
- c7d0cea feat: migrate personal tasks to team_tasks
- b35fe67 feat: team member drag-to-reorder
- 9f90ce6 fix: W logo on team page hard resets to /team
- 5d0271d fix: filter empty and junk values from action_items and leader_tasks
- 949d762 fix: email_intake uses IMAP, bill emails handled as directives with telegram/email clarification routing
- f6e4345 feat: team comms inbound display
- 2d539fe feat: team inbound email digest
- 89fbf30 feat: team contact auto-match sync
- ce42b55 feat: team management system complete
- 31cc741 feat: team cron instructions
- c75ce02 feat: wire team blueprint and mode switcher into dashboard app
- b59aaef feat: team management frontend
- a0e2cce feat: team pre-meeting brief job
- 6737885 feat: team reminders job
- c6d03b2 feat: team email job
- 21ee8d5 feat: team transcript extractor
- 6d7e151 feat: team management API blueprint
- 67515a0 feat: team management DB migration
- a197510 revert: undo all chat keyboard patches, start fresh
- b9c1a9f fix: clean rewrite of chat tab keyboard handling
- 749888b fix: tab-chat full cover with bottom anchor and higher z-index
- eac7f74 fix: resize tab-chat with visualViewport instead of moving input bar
- 4ce1c13 fix: consolidate chat-input-bar CSS, remove conflicting rule
- dab3ccd fix: fixed-position chat input bar with visualViewport keyboard tracking
- c35f00c fix: hide nav bar when chat tab is active
- 5eaf4f7 fix: isolate chat tab as full-screen layer, sticky input bar
- f6dfbbe feat: logins list search input — real-time filter by label, username, url
- 237faa8 fix: use async httpx in handle_vault_callback instead of sync requests
- d4e9109 feat: login vault security — challenge flow, lockout, Telegram unlock
- 282e769 feat: logins manager — DB table, skill, API routes, dashboard overlay
- aa52c7c fix: iOS keyboard pushes chat input off screen — lift tab-chat bottom on viewport resize
- 206cee4 fix: pastoral search colon trigger + campus derivation from attendance history
- 3f17b4d docs: file map 2026-06-23
- 86d32ad docs: architecture update 2026-06-23

### ~/wcky
- 2a96998 fix: shrink popup image on mobile so form is visible on load

---

## Recent Changes — 2026-06-25

### ~/watson
- c7f04cb fix: move briefing to top of more menu below theme toggle
- c5846da fix: both admin users can delete any shared note
- 2bf1f38 fix: show delete button on completed tasks in admin detail panel
- e7e0164 feat: delete task and note buttons in /admin detail panel
- 90d08b6 fix: remove email body note, fix timestamps to localtime in meeting summary handler
- 4cf4289 fix: admin write routes use session user for attribution — drbill vs donna
- bc97874 fix: clarify meeting summary prompt — leader_name is never Bill, tasks split correctly
- 7b1959d fix: first name honorific skip, bill task extraction improvement, log full email body to shared notes
- 4471844 feat: meeting summary handler in email_intake — extracts tasks and logs notes from Bill's whitelist emails
- d0a107c feat: add note input on Notes tab — pastoral or leadership toggle, auto-timestamps
- dcd4d99 feat: notes as second nav tab with expandable cards, briefing moved to more menu
- 29eea6d feat: tap n/a to assign due date on existing dashboard tasks
- c90eea1 fix: date picker and add button equal width on task input row
- 52f6a6a fix: add task input full width, date and button on second line
- e6151d2 feat: optional due date input on dashboard add task
- 1ea903a feat: show due dates on dashboard task rows
- 6707cf2 feat: category reassignment dropdown on dashboard task rows
- 0e1c011 feat: catalyst/fms/personal task tabs on dashboard — FMS and personal private to Bill, Donna sees Catalyst only
- 462bc75 feat: note task scan cron — Watson extracts tasks from shared notes, emails Donna for approval Tue/Wed/Thu 7am
- ce44830 feat: pastoral/leadership toggle on awaiting note intake — routes to pastoral_notes or shared_notes
- 89f677e feat: rolling 36-hour agenda window on dashboard home
- 6f0e619 feat: delete button on awaiting-you items in dashboard
- a70a773 feat: inline pastoral notes in awaiting-you, add task input on dashboard, priority system with Donna control on Bill's tasks
- e1beecf feat: shared notes between /admin and /team — Donna and Bill collaborate per leader, comms removed from admin
- f1e8acf feat: full email triage rework — Watson reads all email, Telegram prompt with ingest/mark-read buttons, reply drafts through existing approval gate, never auto-acts
- 88f0134 feat: 15s auto-refresh polling on dashboard/team/admin + full task list on dashboard home
- 151d734 feat: task reassignment in /admin — Donna can move tasks between any team members
- 61f61ea feat: add team member from /admin — Donna-only, Telegram alert to Bill
- 24a5839 feat: light/dark toggle in /admin header
- 1e29803 feat: /admin team command center with Donna login and auto-status
- bb7ed39 docs: file map 2026-06-24
- c711eda docs: architecture update 2026-06-24

---

## Recent Changes — 2026-06-26

### ~/watson
- c58923d fix: hybrid member threshold raised to 2+ attendances per campus
- 6cfd09b feat: More tab reports — congregation and leadership query buttons
- a55dd8c fix: wdb_query tasks-by-leader keyword expansion
- 71e4aec feat: wdb: skill — leadership team intelligence queries against watson.db
- c3c3b77 feat: cdb_query expanded patterns — missed, hybrid, trend, shepherding attention
- 2bdf822 fix: cdb_query — drop campus/date from output when campus filter specified
- 036d432 feat: cdb_query pattern matcher — bypasses Ollama for attendance queries, correct campus/date filtering
- 3890ef5 fix: cdb_query — explicit date literals, no SQL date functions, no connect_cards join for counts
- fd20c1c fix: cdb_query date calculation and multi-week range support
- 15864bd fix: cdb_query prompt — remove db name prefix from table references
- 77060b3 feat: directive intercepts at top of handler + directive dropdown in dashboard chat
- cd49381 feat: cdb: skill — natural language congregation DB query
- 0d44220 fix: team page title Watson and W favicon matching index.html
- 06ba4ae fix: admin header W logo links to /admin not /
- 0676dab feat: leadership notes in dashboard tied to specific leader, appear in admin shared notes panel
- 21d55b8 fix: rename admin console header to Watson Admin
- 9d90478 feat: Watson W favicon on dashboard and admin console
- 09677b2 fix: skip-all telegram routing, awaiting-you delete button, pre-meeting brief event blocklist
- 7d8b7f4 feat: inline due date editing on tasks in admin panel
- e2b45bd fix: sort admin tasks priority 1 first (most urgent) in leader detail panel
- 7090011 feat: task checkbox completion with 1-hour archive, two-line task layout with priority+date on all tabs
- 143f701 feat: sync catalyst tasks between /team and /admin, add priority + due date display with sort to dashboard
- 68f0468 feat: catalyst-only task count in Bill's admin profile, 1-5 priority scale replacing high/medium/low
- 6004368 fix: working gcal web auth flow via requests_oauthlib
- bd977d4 fix: pass PKCE code verifier through session in gcal auth flow
- e49a8a1 fix: gcal auth flow — disable PKCE, force https in callback url
- 4945406 feat: web-based gcal reauth flow at /gcal-auth
- 89df23e docs: file map 2026-06-25
- 6373fef docs: architecture update 2026-06-25

### ~/wcky
- fa79025 feat: add /thewrongjesus launch page with countdown and email capture

---

## Recent Changes — 2026-06-27

### ~/watson
- 3150c63 fix: capture devloop SSH output locally instead of remote redirect
- c30243d fix: drop start /b, run loop.py synchronously over SSH
- c5ad18d fix: Windows-compatible remote_cmd in dev_loop trigger
- bdacbb4 feat: Watson Dev Loop system — FMSPC autonomous code generation
- 5aadab4 feat: member status system — deceased/disconnected/non-local/snowbird categories, auto-reinstatement on attendance, reporting exclusions
- 11e0e11 feat: State of the Church button in dashboard reports
- 2265c42 feat: church events log — dashboard entry form with file attachments, state of church integration
- bc50027 fix: force English only in Watson's Read synthesis prompt
- b36996d feat: add trends section — rolling averages, campus mix, member engagement tiers
- 24e1455 fix: clarify Catalyst has Wilmington campus and Online campus in synthesis prompt
- eb5e0ff fix: remove duplicate Watson's Read paragraph, clean members not seen formatting
- 45560de fix: members not seen query — drop visitor status filter
- 5081fd4 feat: state of church report — HTML email with professional formatting, synthesis first
- ef63605 fix: condense Ollama input and raise timeout to 180s — prevents synthesis timeout
- e85879c fix: remove tasks and pastoral notes from state of church report — elder-safe version
- baa3d21 feat: remove pastoral notes from state-of-church report
- b0c9d69 feat: upgrade Ollama models — 14b for accuracy jobs including email drafts, 7b for speed/chat/intent
- 75dac3c fix: remove morning briefing Telegram push
- 15ad620 docs: file map 2026-06-26
- 3cac75b docs: architecture update 2026-06-26

---

## Recent Changes — 2026-06-28

### ~/watson
- ba01202 feat: remove skill confirmation gate from dashboard chat — execute immediately
- 64be919 fix: skill cards inherit correct dark theme variables
- 558e97d fix: default to dark theme when no localStorage value set
- b6c14d4 fix: skill card text color follows theme
- c00eb7b fix: skill cards full-width — fix container layout
- e00b91c fix: skills cards iOS tap — use button elements and global launchCommand
- 930ad48 feat: dev loop reads existing file on keep-going — context preserved across iterations
- 32fed04 fix: keep-going passes existing code as feedback to prevent context loss
- 9762219 fix: use llama3.2:3b for session summarization and chat fallback
- dde68ac fix: chat fallback uses llama3.2:3b not qwen2.5:7b
- d44a4e2 fix: stronger no-hallucination guard for empty context
- 423e93e fix: Layer 2 filters junk sessions and raises score threshold to 2
- 2daa981 fix: escape single quotes in Layer 1 no-hallucination guard
- 61370bf fix: add no-hallucination guard to Layer 1 identity block
- e1e8a14 feat: wire build_prompt into bot Ollama calls — persistent memory now active in Telegram
- 3571e79 feat: Watson memory injection system — build_prompt() with 4-layer architecture
- 229425b fix: 48hr auto-cleanup for delivered/failed/stopped dev loop projects
- 385b7fc chore: remove dead reports tab and styles
- c955395 feat: repurpose skills tab as command launcher
- ea4cade fix: iOS clipboard fallback in devLoopCopyCode
- 42b4fb9 fix: syntax-only test in loop.py + dev loop cleanup job
- 3aaab42 fix: replace execution test with syntax check in loop.py
- 2a4b95b fix: load watson .env in loop.py for WRITING_ROOM_API_KEY
- 0ee0e81 refactor: move dev loop from FMSPC SSH to local Beelink execution
- b546d84 docs: file map 2026-06-27
- ad2428d docs: architecture update 2026-06-27

---

## Recent Changes — 2026-06-29

### ~/watson
- fea0062 feat: cdb_query — add 7 new pattern blocks, eliminate Ollama fallthrough for common queries
- c87850f feat: wire all 10 directive prefixes to terminal endpoint; replace gemini_coder with Dev Loop trigger
- fdfabf5 feat: Skills tab auto-fires self-contained commands, loads input-required commands into chat
- acaa598 fix: Skills tab commands route to /api/terminal instead of chat
- 727d0f1 fix: conflict_check fires async from dashboard — no timeout
- 5f3b606 fix: Brenda Boling name correction + conflict merge direction buttons (Keep Old/Keep New/Skip)
- 870f14e docs: file map 2026-06-28
- 2efbb9e docs: architecture update 2026-06-28
