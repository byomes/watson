# Watson Architecture
*Single source of truth. Last updated: July 13, 2026.*
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
- **Ollama:** Bound to `0.0.0.0`. Models: `llama3.2:3b` (primary chat/intent), `qwen2.5-coder:7b` (Dev Loop, KB, structured reasoning), `qwen2.5:7b` (accuracy-sensitive background jobs — pastoral notes, email drafts, task/goal extraction, State of Church synthesis, skill/capability audits), `phi3:mini` (background tasks), `gemma3:1b` (fast/lightweight)

### FMSPC — Windows Desktop (GPU Tasks Only)

- **Specs:** RTX 3070 Ti (8GB VRAM), 128GB RAM
- **Role:** Whisper transcription only.
- **Whisper:** faster-whisper, Whisper Large-v3
- **Not always-on.** FMSPC env vars left in `.env` for future use.
- **⚠️ FMSPC is excluded from Watson's automated job loop, permanently — standing
  decision, not a temporary state.** No cron job, Telegram-triggered job, or
  dashboard-triggered job may call an Ollama model hosted on FMSPC. Every
  automated Ollama call must target the Beelink's own `localhost:11434` with a
  model sized for the Beelink (32GB RAM, no dedicated GPU) — see LLM Stack
  below. Root cause history: `qwen2.5:14b` (9GB, 14.8B params) was originally
  routed to FMSPC for "accuracy-sensitive" jobs, but FMSPC isn't always on, so
  in practice those jobs silently fell back to requesting `qwen2.5:14b` against
  the Beelink's own `localhost:11434` instead — loading a model that heavy into
  the Beelink's RAM starved concurrent Ollama calls (e.g. simple intent
  classification) of scheduling priority, causing intermittent multi-second-to-
  60+-second hangs across unrelated Telegram queries. Traced and fixed
  2026-07-16 (12 files moved to `qwen2.5:7b` on the Beelink); this note exists
  because the fix has been lost between sessions once already — if a future
  build proposes routing any automated job to FMSPC, that is the bug, not a
  valid solution.

  **2026-07-17 update:** `OLLAMA_MAX_LOADED_MODELS` was raised from `1` to `3`
  today (`/etc/systemd/system/ollama.service.d/override.conf`). This fixes
  *part* of the original hang mechanism — with `=1`, requesting a second model
  while a heavy one was loaded forced an evict-and-reload cycle (cold loads
  measured 22–80s in `memory/model_benchmark_20260715.md`), which was likely
  the dominant cause of the observed 60+-second stalls. With `=3`, the intent
  classifier and a heavy background-job model can now stay resident
  simultaneously without thrashing.

  **This does NOT fully resolve the original risk.** `OLLAMA_NUM_PARALLEL` is
  still `1` — Ollama still serializes all generate requests one at a time,
  system-wide, regardless of how many models are resident in memory. A
  long-running call on a heavy model still blocks every other Ollama request
  (including intent classification) for its full duration.

  Because of that gap, `qwen2.5:14b` remains off every Beelink job for now —
  **not because the 2026-07-16 fix failed**, but because concurrent-load
  behavior was never re-tested after the `MAX_LOADED_MODELS` change. (A
  same-day attempt to reintroduce `qwen2.5:14b` into `state_of_church.py` and
  `draft_email.py` was made and then reverted before being committed — see the
  commit that added this note.) Before `qwen2.5:14b` is reconsidered for any
  job, someone needs to actually test it under real concurrent load: fire a
  real Telegram message through `classify()` while a long `qwen2.5:14b` call
  is mid-run, and confirm `classify()`'s 10s timeout/fallback (bug #20,
  `56d60dd`) behaves correctly under that real contention — not just in
  isolation.

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
| bodyrec | `github.com/byomes/bodyrec` | `~/bodyrec` | Vercel auto on push |

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
| `bodyrec.vercel.app` | `byomes/bodyrec` | Body composition tracker (bill/mel profiles), backed by Watson API |

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
- `body_entries`, `body_settings` — bodyrec body composition tracker data

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
| `llama3.2:3b` | Beelink Ollama | Primary Watson chat (Telegram general-chat fallback), session summarization |
| `gemma3:4b` | Beelink Ollama | Intent classification (Telegram only, `jobs/intent/classifier.py`) — swapped from `llama3.2:3b` 2026-07-17 (bug #20, `56d60dd`); `keep_alive=30m` plus `jobs/intent/keep_warm.py` cron (every 4 min) keep it resident |
| `qwen2.5-coder:7b` | Beelink Ollama | Dev Loop, KB search, structured reasoning |
| `qwen2.5:7b` | Beelink Ollama | Accuracy-sensitive background jobs: pastoral notes, meeting/note task+goal extraction, email drafts, State of Church synthesis, skill/capability audits, elder-review meeting summaries |
| `phi3:mini` | Beelink Ollama | Background tasks |
| `gemma3:1b` | Beelink Ollama | Fast/lightweight queries |

**No Claude API calls in automated Watson jobs.** Ollama handles all automated inference.

**Retired — `qwen2.5:14b` (FMSPC Ollama):** was listed here for "accuracy-sensitive"
jobs, but FMSPC isn't always on, so those jobs actually ran `qwen2.5:14b` against
the *Beelink's* `localhost:11434` — a 9GB/14.8B-param model too heavy for the
Beelink, which starved concurrent Ollama calls and caused intermittent
multi-second-to-60+-second hangs (root-caused 2026-07-16). Replaced by
`qwen2.5:7b` on the Beelink across all 12 call sites. See the FMSPC note under
Hardware — FMSPC is excluded from the automated job loop entirely, permanently.
Still off every Beelink job as of 2026-07-17 despite that day's
`OLLAMA_MAX_LOADED_MODELS` bump — see the 2026-07-17 update under the FMSPC
note for why that change isn't sufficient on its own to bring it back.

---

## Integrations

| Service | Purpose | Notes |
|---------|---------|-------|
| Google Calendar | Scheduling, booking, pre-meeting briefs, wcky `/meet` availability + booking | OAuth2 (Watson-Web client, `watson-498401` project), `~/watson/config/token.json`, scope: Calendar. Shared by wcky's Vercel env vars — not a separate credential. |
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
| `jobs/connect_cards/email_reports.py --bill --prayer --kaci` | Mon 5am | Next steps/comments → Bill; prayer digest → Bill; prayer requests report → Kaci |
| `jobs/connect_cards/email_reports.py --donna` | Tue 5am | Attendance → Donna |
| `jobs/connect_cards/attendance_intake.py` | Every 30 min | Attendance intake |
| `jobs/connect_cards/correction_handler.py` | Every 30 min | Attendance corrections |
| `jobs/connect_cards/campus_classifier.py` | Mon 5:45am | Classify member campus from 8-week connect card history |
| `jobs/connect_cards/missed_report.py` | Mon 6am | Missed report — 3 sections: Wilmington, Online, Hybrid — recipients: Bill, Donna, Kaci |
| `jobs/connect_cards/shepherding_report.py` | Wed 6am | Pastoral care digest |
| `jobs/connect_cards/conflict_report.py` | Sun 5pm | Member conflict report with 3-button Telegram resolution |
| `jobs/connect_cards/state_of_church.py` | Thu 4pm | State of the Church HTML email |
| `jobs/email_intake.py` | Every min | Gmail polling + triage |
| `jobs/email_reply/reader.py` | Every 15 min | Email reply handler |
| `jobs/reminders/daily_summary.py` | 10am, 1:30pm, 5pm (Mon–Sat) | Daily reminders |
| `jobs/reminders/check_timed.py` | Every 5 min | Timed reminder checks |
| `jobs/gcal/token_health.py` | Daily 7am | Google OAuth token health check (Watson's own `token.json`) |
| `jobs/gcal/meet_token_health.py` | Daily 7am | wcky `/meet` availability endpoint health check (live HTTP probe, not local token check) |
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

## Body Composition Tracker (bodyrec)

`bodyrec.vercel.app` — body composition tracker with separate bill/mel profiles.

**Architecture:** Next.js frontend (`~/bodyrec`) → Watson API → `watson.db`
**Watson API base:** `https://watson.tail0243ff.ts.net` (Tailscale Funnel)
**Auth:** `X-Watson-Key` header shared secret — same pattern as Writing Room
**Watson job file:** `jobs/bodyrec/api.py` — Flask blueprint, routes registered on dashboard app
**Tables:** `body_entries`, `body_settings`
**Supabase:** Fully retired for this project — all data and API needs route through Watson (`watson.db`), no Supabase dependency remains.

---

## The Wrong Jesus (Book)

- **Status:** Manuscript complete. Reader retired from `/twj/read` (2026-07-01, TWJ/ARC consolidation) — now read via ARC manuscript reader at `/arc/dashboard`.
- **Manuscript time-lock** (`src/lib/launch-dates.ts`): unlocks 2026-07-15, closes 2026-09-14 (standalone constant, 2 hours before `TWJ_LAUNCH_DATE`, not pinned to it). Admin-preview bypass (`is_admin_preview`) available to view outside the window.
- **Manuscript gate enforcement:** Manuscript access is gated server-side via Next.js SSR in `wcky/src/app/arc/dashboard/page.tsx` (`getManuscriptStatus()`). The Watson Python backend (`jobs/arc/`) has no manuscript-serving route and is not part of this gate. Verified 2026-07-14.
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
- `/api/terminal` is a **separate manual command-console endpoint**, not the chat path —
  handles its own prefix set: `cdb:`, `wdb:`, `web:`, `bible:`, `polish:`, `polish this:`,
  `kb:`, `build:`, `debug:`, `run:`, plus `_TERM_COMMANDS` lookups
- `/api/chat/stream` (the actual chat path) independently reimplements its own early
  prefix intercepts — `cdb:`, `wdb:`, `web:`, `bible:`, `polish:`, `bug:`, `gutenberg:`,
  `classics:`, `build:`, `debug:`, `run:` — plus separate natural-language KB triggers
  ("search kb", "search my notes", etc.) and a time-query intercept, all ahead of the
  identity/factual/conversational checks and the shared router fallback. **All of these
  prefixes also exist on Telegram** via `_DIRECTIVE_PREFIXES` (see Telegram Bot) except
  `build:`, which has a Telegram equivalent under a different name (`devloop:`) — this
  list duplicates Telegram's, it is not a smaller subset of it.
- Anything not caught by the above falls through to `_router.route(message, "dashboard")`
  — see Shared Routing Module below.

### Shared Routing Module (`jobs/skillbuilder/router.py`)
`route(message: str, interface: str) -> dict` is the shared, interface-agnostic routing
core both `bot.py` and `/api/chat/stream` call directly (`_router.route(text, "telegram")`
and `_router.route(message, "dashboard")`). It owns `_SKILL_PRE_CHECKS` (19 entries — not
Telegram-specific despite living conceptually under "Telegram routing" in past docs),
`_BUILD_TRIGGERS`, `_AUDIT_TRIGGERS`, skill-trigger matching, and the LLM-based
`SKILL:`/`LIST_SKILLS`/`BUILD`/`PROPOSE`/`WRAP_UP`/`CHAT` fallback. No Telegram objects,
chat IDs, or reply-threading leak into it — **this already is the shared pipeline; a
future session should not assume "unifying routing" means building this from scratch.**

Both channels layer their own prefix-interception in front of it, and those layers have
drifted into real duplication rather than a capability gap: **Telegram has three
overlapping mechanisms** (`_DIRECTIVE_PREFIXES` → its own `_SKILL_PRE_CHECKS` pre-check
loop → `route()`'s internal `_SKILL_PRE_CHECKS` recheck); **dashboard has two** (its own
early intercepts → `route()`). Several prefixes (`cdb:`, `kb:`, `polish this:`,
`gutenberg:`, `classics:`, `debug:`) are matched redundantly by two or three layers on
Telegram alone. Consolidating all of this into one canonical prefix/trigger table both
channels read from is the actual routing-unification task — see Known capability gap
under Telegram Bot for what's genuinely missing (very little) versus merely duplicated
(most of it).

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
  2. `_DIRECTIVE_PREFIXES` colon-prefix intercepts (`bot.py` ~line 618) — 15 prefixes:
     `cdb:`, `wdb:`, `kb:`, `web:`, `task:`, `note:`, `remind:`, `sms:`, `polish:`,
     `bible:`, `devloop:`, `bug:`, `gutenberg:`, `classics:`, `fireflies:`.
     Highest-priority, Telegram-only mechanism, checked before anything else.
  3. `bot.py`'s own pre-check loop over `_SKILL_PRE_CHECKS` (19 triggers, not 18 — see
     Shared Routing Module under Watson Dashboard) — dispatches 10 of those slugs to
     Telegram-specific rich handlers. Several triggers here (`cdb:`, `kb:`,
     `polish this:`, `gutenberg:`, `classics:`, `debug:`) duplicate stage 2 or 4.
  4. `_router.route(text, "telegram")` — the same shared module dashboard calls;
     re-checks `_SKILL_PRE_CHECKS` plus build/audit/wrap-up/identity/factual/skill-trigger
     logic and an LLM fallback
  5. Ollama intent classifier (`gemma3:4b`, not `llama3.2:3b` — see LLM Stack) —
     **Telegram-only, no dashboard equivalent.** Handles `contact_lookup`,
     `calendar_query`, `calendar_busy`, `calendar_availability`, `block_time`,
     `book_appointment`, `reminder_create`, `task_create`, `task_list`, `task_done`,
     `image_search`.
  6. General Ollama chat fallback (`llama3.2:3b`)

  Stages 2–4 overlap heavily — three separate mechanisms each independently re-match
  `cdb:`/`kb:`/`polish this:`/`gutenberg:`/`classics:`/`debug:`. This is duplication,
  not a capability gap; slated for consolidation into one canonical table (see Shared
  Routing Module).

**Known capability gap (2026-07-17 routing audit, corrected):** of the classifier-stage
intents above, only **`block_time`** and **`calendar_availability`** have no dashboard
path at all. Everything else — including every `_DIRECTIVE_PREFIXES` colon-prefix
(`wdb:`, `bug:`, `web:`, `polish:` included) — is already reachable from both channels,
just via inconsistent syntax and duplicated code paths rather than a real gap.
`contact_lookup` and `reminder_create` are independently reimplemented in dashboard's
`/api/chat/stream`; `book_appointment` and `calendar_busy` exist as dedicated dashboard
REST endpoints/UI rather than chat intents. `build:` (dashboard) and `devloop:`
(Telegram) trigger the identical Dev Loop function under different spellings — a naming
mismatch, not a gap.

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
- **Watson-Web OAuth client:** `717658188112-fjmm14rb3asfutnppql2dldftpd1djc5.apps.googleusercontent.com`, GCP project `watson-498401`, publish status "In production"
- **Calendar ID:** `bill.yomes@gmail.com`
- **Scope:** `https://www.googleapis.com/auth/calendar` (Calendar only — `token.json`'s `scopes` field and the actual `/gcal-auth` authorization call both confirm this; a `gmail.send` entry exists in an unused constant in `app.py` but is never requested)
- **Token health check:** Daily 7am (`jobs/gcal/token_health.py`) — checks Watson's own `token.json` only
- **Reauth (Watson):** `/gcal-auth` web route in dashboard app, or `jobs/gcal/reauth.py` (terminal flow) — both write to `~/watson/config/token.json`

### wcky `/meet` — shares Watson-Web's OAuth client (as of 2026-07-13)

`williamckyomes.com/meet` (`src/app/api/meet/availability/route.ts` and `book/route.ts`, on Vercel) calls Google Calendar **directly from the Vercel serverless function** — it does not route through Watson/Tailscale Funnel at all. It used to run on its own separate OAuth client (`...nb0j56bqpb68g1mbn5oi0lorvkk9g302...`), which had its grant silently revoked on the Google account side and broke repeatedly with no monitoring in place to catch it. It has been migrated to **reuse Watson-Web's OAuth client** above — one client for Bill's calendar, not two.

- **wcky Vercel env vars** (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` — Production + Preview, all type `sensitive` so their values can't be read back via `vercel env pull`/API once set) now hold Watson-Web's client_id/secret and a refresh token minted independently for wcky (does not live in `~/watson/config/token.json` — separate token instance, same client, same scope).
- **Reauth (wcky only):** `~/watson/scripts/wcky_meet_reauth.py` — one-time standalone script, deliberately *not* wired to Watson's `token.json`. Mints a fresh refresh token via a local-loopback OAuth flow (`http://localhost:8765/`, registered as an extra redirect URI on the Watson-Web client for exactly this purpose) and prints it to the terminal for manual copy into Vercel. Run this, then update `GOOGLE_REFRESH_TOKEN` in Vercel (wcky project) and redeploy, if `/meet` availability ever breaks again.
- **Health check:** Daily 7am (`jobs/gcal/meet_token_health.py`) — unlike `token_health.py`, this does a live HTTP probe of `https://www.williamckyomes.com/api/meet/availability?duration=30` and confirms real slot data comes back, since wcky's credential lives entirely in Vercel and isn't visible to any local check. Alerts via the same Telegram path as `token_health.py`.
- **Old client not yet deleted:** `...nb0j56bqpb68g1mbn5oi0lorvkk9g302...` still exists in Google Cloud Console (`watson-498401`), kept intentionally for a few days post-migration before manual deletion — do not delete without checking with Bill first.

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

## Pre-Tracker Resolved Items (reconciled 2026-07-12)

Bugs surfaced in Claude.ai conversation history predating the `bug_tracker` table (created 2026-07-09, `4ba9e30`) and confirmed fixed against current code during a 2026-07-12 reconciliation pass. Not backfilled as `bug_tracker` rows — they were already closed before the table existed, and inserting them now would misrepresent the table's actual discovery-to-fix usage history. Logged here instead for a complete record.

| Item | Resolved | Commit |
|------|----------|--------|
| Dashboard briefing tab buttons not fully functional | 2026-06-11 | `f4fda9a` |
| Scheduler publishing drafts immediately instead of queuing | 2026-05-11 | `f6bb959` |
| Facebook queue confirmation message too verbose | 2026-05-17 | `179700d` |
| `run:image_search` routing to QR generator instead of image search | 2026-06-10 | `fee3d1d` |
| `kb/transcripts` gitignore / broken Telegram URL on archiving | 2026-06-20 (superseded by rewrite) | `2324529` |
| `.btn-save` CSS renders blue on briefing static template | 2026-06-04 | `7c6e4bc` |
| `pastoral_notes` insert schema mismatch (`person_id` etc.) | 2026-06-20 | `61f0de7` |
| Task handler silently swallows exceptions, no Telegram error reply | 2026-07-09 | `2bea12f` |
| Dead `refine:` code in BUILD pipeline (91 lines) | 2026-06-20 | `b93db3e` |
| Task router misrouting "add a task" to BUILD system | 2026-06-11 | `fc0949a` |
| Siri shortcut pointing at LAN IP instead of Tailscale IP | unconfirmed (device-side iOS Shortcuts setting, not a code fix) | N/A — documented correct in `03143bf` (2026-06-13) |

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
17. Watson self-improvement system — architecture approved, build deferred

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

---

## Recent Changes — 2026-07-13

### ~/watson
- a0417bf fix: same_name_diff_email now creates mergeable member record; add Two Different People resolution
- 9cbb53c feat: add project_backlog to Dev tab, reopen bug #11 pending real Gutenberg fix
- 7230e5b feat: rename Issues tab to Dev, split into Dev/Bugs sub-tabs
- db48502 docs: reconcile pre-tracker resolved items, remove stale GitHub token item
- e964548 feat: flag stuck 'running' Dev Loop projects for manual review
- ae43a3c feat: add BodyRec tile to dashboard More menu, links to bodyrec.vercel.app
- b78441b fix: add ~/bodyrec to file_map.py REPOS so FILE_MAP.md keeps tracking it
- 5ef79d6 docs: add bodyrec as tracked repo/web property in architecture doc
- 3b039e6 docs: file map 2026-07-12
- c27bbb4 docs: file map 2026-07-12
- 6100585 chore: ignore .env.backup-* and *.db.backup-* files
- ddabe9b fix: match More tab spacing above tile grid to grid's internal row-gap
- 85e044f docs: file map 2026-07-12
- 084928c docs: architecture update 2026-07-12

---

## Recent Changes — 2026-07-14

### ~/watson
- e627581 fix: require X-Watson-Key auth on /api/book-appointment now that it's reachable via public Funnel
- b90e6ee docs: update Mon/Tue email report schedule, Kaci added to missed report
- c1438a5 docs: architecture update 2026-07-13 (auto-generated, recovered from failed push)
- cd01708 feat: add Kaci as recipient to missed attendance report
- 4aa5df2 docs: file map 2026-07-13
- 8aa85a2 docs: architecture update 2026-07-13

### ~/wcky
- 8592963 fix: Pastor Bill to Dr. Bill on /meet page
- 395c951 feat: remove 60-minute duration option from /meet booking
- 5c43aaa copy: /meet confirmation email says Dr. Bill, not Pastor Bill
- 0b7cfe2 fix: /meet OG/Twitter preview image never rendered — wrong image + broken redirect
- d006f60 fix: guard /meet booking against double-submit with a synchronous ref
- a0c49d5 fix: /meet booking store call was hitting an unreachable Tailscale IP

---

## Recent Changes — 2026-07-15

### ~/watson
- e505d95 feat: gutendex.service unit file for self-hosted Gutenberg catalog API
- 4331504 docs: document ARC manuscript gate enforcement point + close date fix
- 852d533 fix: elder-review auto-created tasks used category='elders', invisible to Donna's Team Admin
- 4bfc6e4 fix: Save button on meeting review page gave no confirmation on click
- 127f3b4 fix: elder-review Ollama structured JSON timeout, and fallback item merging
- ba129a2 feat: Meeting Reviews list page + More menu entry
- e9cec07 feat: auto-create team_tasks on Approve & Send for tracked elder-review owners
- 9030702 feat: restrict elder-review owner dropdown/fuzzy-match to 8 named people
- c1a3fe6 feat: dashboard review-and-edit page for elder meeting reviews
- b5cd088 feat: adapt elder_review.py template for the dashboard-editable review schema
- 824dc7c feat: fireflies_review.py pipeline -> dashboard-review-first flow
- 607977b feat: meeting_reviews + meeting_review_action_items schema (watson.db)
- 6af28f9 feat: professional HTML template + live preview for elders review emails
- cddd0ca fix: fireflies: directive times out — 60s Ollama timeout + 15s handle_text wrapper
- bd17d25 feat: manual trigger for the Fireflies elder-review pipeline
- 139ec1d feat: add deacon and partner quick-add buttons to Member Management Roles
- 7a9c3f3 fix: Fireflies webhook reads "event" key, not "eventType"
- db423b8 fix: Fireflies webhook signature always rejected — unstripped sha256= prefix
- 2427104 fix: Home dashboard task list missing priority/due-date edit controls
- 57d027f feat: Fireflies.ai elder meeting review pipeline
- 17e39c8 feat: leadership role tagging — leadership_roles table + Member Management Roles control
- 05dacf1 fix: team task priority editing blocked by category gate + isBill UI gate
- 512a0b0 Merge branch 'main' of https://github.com/byomes/watson
- af0d702 fix: remove Kaci as recipient of prayer requests report, keep attendance report
- be18664 docs: file map 2026-07-14
- 7cf5642 docs: architecture update 2026-07-14

### ~/wcky
- df6c4e1 feat: show countdown + commitments list on ARC dashboard pre-unlock
- 6ed2384 docs: note manuscript gate enforcement lives in SSR page, not Watson backend
- b39728c Close ARC manuscript access the night before launch, not at launch instant
- abc3908 Push ARC manuscript unlock time to 8am Eastern
- beea900 publish: The Dry Ground Is Under Your Feet Only After You Step In

---

## Recent Changes — 2026-07-16

### ~/watson
- d8c0c50 feat: SMS carrier lookup + email-to-SMS gateway with manual-confirm fallback
- d124dc1 fix: raise ask.py synthesize() timeout to 240s — cold-start qwen2.5-coder:7b load exceeded 120s
- be7ab8c fix: router.py accuracy fix (qwen2.5-coder:7b), resolve bug #18 — ask.py KB search model swap
- 25963e3 chore: retire dead files with unreferenced qwen2.5:14b calls (page_generator.py, content_calendar.py)
- 4f113cc fix: route model calls off qwen2.5:14b — llama3.2:1b for classification, qwen2.5-coder:7b for code-adjacent tasks, FMSPC fully removed from architecture
- 7985876 feat: add partnership_status field (Guest/Regular Attender/Partner) to members
- 4157354 fix: correct Writing Room email sign-offs to Watson (arc welcome + call reminder)
- d15acdf feat: ARC self-service forgot-password + split email templates
- 7a7aec0 feat: Batch Update Members panel + read-only alias display (Member Management)
- f6bf51b feat: batch member update engine + per-member nickname aliases (cdb: mark / alias)
- 1fd5e8a docs: file map 2026-07-15
- 7a35db2 docs: architecture update 2026-07-15

### ~/wcky
- 35f83da feat: ARC self-service forgot-password page

---

## Recent Changes — 2026-07-17

### ~/watson
- 528de38 feat: display-only name title-case formatter, wired into missed/shepherding reports; add find_malformed_names.py audit script
- ac37973 fix: state_of_church.py — range-verdict comparison bug, timeout bump, display-name formatting
- 167eaca feat: state_of_church.py — rolling avg trend band, seasonal caveats, benchmarks context in synthesis prompt
- f8184c3 feat: weekly church attendance benchmark check — Serper scan, Telegram approval, benchmarks.md doc
- 39e1082 fix: replace qwen2.5:14b with qwen2.5:7b across all Beelink jobs (root cause of intermittent hangs), update architecture doc
- 1eebd86 fix: remove redundant partner role chip (tracked via partnership_status instead)
- dd03313 feat: dashboard name editing (members) + carrier editing (members/people) via phone_carriers cache; new people/contacts panel
- 5c9577b fix: never auto-trust unconfirmed carrier guesses, always require manual confirmation
- 8d8e55e feat: autonomous SMS auto-reply via Ollama for inbound texts, with Telegram exchange log
- cb1dc6f fix: pastoral search name extraction (colon/for phrasing), vcf import case-sensitive dedup
- 1d84911 feat: forward last/inline Telegram content to any contact via SMS/email
- 31ec9ce feat: detect inbound SMS replies by sender address shape, bypass triage
- 424af55 feat: resolve me/myself to Bill's own contact via telegram_chat_id
- a42e31e docs: file map 2026-07-16
- 61b2185 docs: architecture update 2026-07-16
