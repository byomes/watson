# Watson System Memory
*Last updated: June 13, 2026*

---

## What Watson Is

Watson is Dr. Bill Yomes's personal AI assistant system — not a single bot, but an orchestrated ecosystem of jobs, hardware, and interfaces. Watson runs **jobs**. There are no sub-agents, no agent personas, no named bots (Charles, Jenny, Mark are all retired).

Watson is an **AI-powered digital assistant**. The LLM provides language and reasoning; everything else is scripts and tools. Watson acts on Dr. Bill's behalf under his supervision, is always identified openly as Watson, never pastors or speaks theologically without permission, never guesses, and always asks for clarity.

**Non-negotiable constraints:**
- No hallucination. If Watson does not know, Watson says so and stops.
- Not an image bearer. No soul, no Holy Spirit access, no spiritual discernment. Cannot pastor, counsel, or speak with spiritual authority under any framing.
- Clarity over assumption. Ask one specific question and wait rather than proceed on assumptions.

---

## Hardware

### Beelink EQi12 (Primary Watson Server — LIVE)
- **Specs:** Intel i5 12th gen, 32GB DDR4 RAM, 500GB NVMe SSD
- **OS:** Linux Mint XFCE
- **Hostname:** `watson` | **User:** `billyomes`
- **Network IP:** `192.168.1.204` | **Tailscale IP:** `100.117.237.96`
- **Tailscale hostname:** `watson.tail0243ff.ts.net`
- **SSH:** `ssh billyomes@watson` or `ssh billyomes@192.168.1.204`
- **Ollama:** Installed, bound to `0.0.0.0`. Models: `llama3.2:3b` (chat fallback), `qwen2.5-coder:7b` (code/reasoning), `phi3:mini` (tasks), `gemma3:1b` (lightweight)
- **Watson repo:** `~/watson`
- **Watson DB:** `~/watson/data/watson.db`
- **Congregation DB:** `~/watson/data/congregation.db`
- **Tailscale Serve:** `https://watson.tail0243ff.ts.net` → proxies to `http://localhost:5200`

### FMSPC (Windows Desktop — GPU only, not always on)
- **Role:** GPU transcription only (Whisper Large-v3). NOT used for Watson jobs. Machine is Dr. Bill's primary video editing workstation — protect it.
- **Specs:** RTX 3070 Ti, 128GB RAM
- **Files:** Watson codebase mirror at `C:\Users\billy\OneDrive\Claude\agents\watson`

### Stream (HP Stream — Retired)
- Replaced by Beelink. Offline.

---

## Interface Strategy

### Primary (Home)
- **Watson Dashboard** at `http://192.168.1.204:5200` — chat, briefing, tasks, reminders, reading list, contacts
- Saved to iPhone Home Screen as PWA webapp

### Away from Home
- **Telegram** — commands, urgent alerts, quick queries, briefing delivery, Bible lookup, build pipeline, pastoral notes replies
- **Siri shortcut** — "Tell Watson" → `100.117.237.96:5200/api/siri`

### Dashboard Access
- Home: `http://192.168.1.204:5200`
- Tailscale (anywhere): `http://100.117.237.96:5200`
- Public redirect: `williamckyomes.com/dashboard` → `https://watson.tail0243ff.ts.net`

---

## LLM Strategy

| Layer | Tool | Use |
|---|---|---|
| Claude.ai | claude.ai browser | Strategy, architecture, diagnosis, spec writing, quality writing |
| Claude API | `claude-sonnet-4-6` | Primary Watson chat engine (dashboard + bot), build pipeline final review |
| Claude API | `claude-haiku-4-5-20251001` | Build pipeline spec drafting (cheap, fast) |
| Claude Code | `--dangerously-skip-permissions` | Sole code executor — file editing, building jobs, committing |
| Ollama `llama3.2:3b` | Beelink | Chat fallback if Claude API unavailable |
| Ollama `qwen2.5-coder:7b` | Beelink | Build pipeline spec fallback if Claude API unavailable |

**Budget:** $20/month Claude overall. Claude API used only for: chat inference, spec drafting (Haiku), spec review (Sonnet), final build review (Sonnet). Estimated actual cost: ~$2-3/month at current volume.

**Gemini is permanently removed from the coding loop** — made too many mistakes including file corruption. Claude Code is sole executor.

**FMSPC is not available** for Watson jobs — it's not always on and must be protected for video editing.

---

## Active Services (systemd)

| Service | Purpose |
|---|---|
| `watson-bot.service` | Telegram bot |
| `watson-dashboard.service` | Flask dashboard on port 5200 |
| `watson-people.service` | People Registry HTTP API on port 5100 |
| `watson-codeagent.service` | Code Agent confirm listener |

**Restart commands:**
```
sudo systemctl restart watson-bot watson-dashboard watson-people
```

---

## Cron Jobs

All cron entries use `PYTHONPATH=/home/billyomes/watson`. Set at top of crontab globally.

| Schedule | Job | Purpose |
|---|---|---|
| Daily 10am | `jobs/scheduler.py` | Publish blog drafts where scheduled_date = today |
| Every 15 min | `jobs/ingest_drafts.py` | Poll Upstash KV for new blog drafts |
| Daily 6am | `core/pipeline.py` | Main content pipeline + briefing |
| Every 15 min | `jobs/facebook/facebook_post.py` | Facebook post queue processor |
| Every 30 min | `jobs/connect_cards/intake.py` | Parse Subsplash connect card emails |
| Every 30 min | `jobs/connect_cards/attendance_intake.py` | Attendance sync |
| Every 30 min | `jobs/connect_cards/correction_handler.py` | Handle correction replies |
| Mon 5am | `jobs/connect_cards/email_reports.py --bill` | Prayer requests + follow-ups to Dr. Bill |
| Tue 5am | `jobs/connect_cards/email_reports.py --donna --kaci` | Attendance to Donna and Kaci |
| Sun 4am | `jobs/connect_cards/email_reports.py --sync` | Silent attendance sync |
| Mon 6am | `jobs/connect_cards/missed_report.py` | Missed report |
| Mon 6am | `jobs/connect_cards/shepherding_report` | Pastoral care digest |
| Every min | `jobs/email_intake.py` | Gmail polling, Ollama triage, urgent Telegram alert |
| Every 15 min | `jobs/pastoral_notes/prompt.py` | Detect ended appointments, send Telegram note prompts |
| Every 15 min | `jobs/pastoral_notes/reminder.py` | 2-hour reminders for unanswered note prompts |
| Every 5 min | `jobs/reminders/check_timed.py` | Check timed reminders |
| 10am/1:30pm/5pm Mon-Sat | `jobs/reminders/daily_summary.py` | Reminder summaries |
| Every 15 min | `jobs/email_reply/reader.py` | Read and draft email replies |
| Thu 7am | `jobs/email_job/draft_email.py` | Weekly email draft |
| Every 5 min | `jobs/memory/sync.py` | Memory sync |
| Every 15 min | `jobs/monitoring/log_watch.py` | Log monitoring |
| Mon 7am | `jobs/skillbuilder/audit.py` | Weekly skill audit |
| 1st/15th 2am | `jobs/skillbuilder/audit.py` | Bi-monthly skill audit |

---

## Codebase Structure

**GitHub:** `github.com/byomes/watson`
**Beelink path:** `~/watson`

```
~/watson/
├── bot/bot.py              — Telegram bot (watson-bot.service)
├── config/settings.py      — env vars, WATSON_SYSTEM prompt
├── core/
│   ├── pipeline.py         — main content pipeline (6am daily)
│   ├── database.py         — DB connection helpers
│   └── scorer.py           — briefing article scorer
├── jobs/
│   ├── dashboard/app.py    — Flask dashboard (port 5200)
│   ├── dev/                — build pipeline and dev tools
│   ├── connect_cards/      — church connect card pipeline
│   ├── pastoral_notes/     — post-appointment note prompts
│   ├── people/             — people registry (port 5100)
│   ├── gcal/               — Google Calendar integration
│   ├── bible.py            — Bible lookup (NIV/CSB/NASB)
│   ├── email_intake.py     — Gmail polling + triage
│   ├── email_reply/        — email drafting + approval
│   ├── email_job/          — weekly email draft
│   ├── facebook/           — Facebook post queue
│   ├── qr/                 — QR code generation
│   ├── reminders/          — reminder system
│   ├── skillbuilder/       — skill router, builder, auditor
│   ├── skills/             — skill implementations
│   ├── tasks/              — task management
│   └── research/           — web search, article reader, etc.
├── memory/
│   ├── skills.json         — skill registry
│   ├── builds/             — build memory archive + BUILD_INDEX.md
│   ├── coding/             — coding lessons (python.md, etc.)
│   ├── architecture.md     — system architecture notes
│   └── projects/           — project memory
├── kb/
│   ├── documents/          — 453 KB docs (sermons, notes, handouts)
│   └── transcripts/        — sermon transcription output
└── data/
    ├── watson.db           — primary operational DB
    └── congregation.db     — church CRM (authoritative)
```

---

## Databases

### watson.db (~/watson/data/watson.db)
Primary operational database. Tables:
- `tasks` — task management
- `reminders` — reminder system
- `chat_sessions` / `chat_messages` — conversation history
- `blog_drafts` — blog draft queue
- `facebook_queue` — Facebook post queue
- `email_queue` — email triage queue
- `connect_cards` — parsed connect cards
- `people` — personal contacts registry
- `pastoral_notes` — post-appointment notes
- `notes_pending` — unanswered note prompts
- `reading_list` — reading list
- `build_approvals` — pending build pipeline approvals
- `capability_gaps` — proposed new skills
- `voice_notes` — voice note transcripts

### congregation.db (~/watson/data/congregation.db)
Authoritative church CRM. Tables: `members`, `connect_cards`, `attendance`, `follow_ups`, `prayer_requests` (with `leadership_only` column), `next_steps`, `duplicate_flags`.

**Rule:** All member lookups use `congregation.db` first, `watson.db` people table as fallback. The `watson.db` congregation table is redundant/legacy.

---

## Build Pipeline (jobs/dev/build_pipeline.py) — LIVE as of June 13, 2026

Full autonomous build loop triggered from Telegram. Human touch: one `approve`.

**Trigger:** Send `build [natural language description]` in Telegram

**Pipeline steps:**
1. **Spec draft** — Claude Haiku (`claude-haiku-4-5-20251001`) writes a structured Claude Code spec. Falls back to Ollama `qwen2.5-coder:7b` if API unavailable.
2. **Spec review** — Claude Sonnet reviews spec for blocking issues only (wrong file, new Flask instance, bad imports, auth/credentials touch, force push). Auto-retries once with required changes incorporated. If 2nd attempt fails, stops and notifies Telegram.
3. **Claude Code execution** — runs `claude --dangerously-skip-permissions < spec_file` at `/home/billyomes/.nvm/versions/node/v24.16.0/bin/claude`. 300s timeout.
4. **Local test** — detects modified .py file via `git diff HEAD~1 --name-only`, imports it via `importlib` (no server start), confirms "Import OK".
5. **Final review** — Claude Sonnet reviews code diff + test output + spec. Returns structured JSON: recommendation, confidence, assessment, risks, strengths, deployment_safety. Sends full review to Telegram and waits for approval.
6. **Approval gate** — Dr. Bill replies `approve` or `refine: [feedback]`. On approve: git commit + push, then `build_memory_store.py` archives the full build record.

**Hard blocks:** Requests containing `auth`, `password`, `secret`, `token`, `credentials`, or `build_pipeline` are rejected immediately.

**Build memory:** Every successful deployment archived to `~/watson/memory/builds/[YYYYMMDD-HHMMSS-name]/` with spec, diff, test output, Claude review, approval, deployment log. Index at `~/watson/memory/builds/BUILD_INDEX.md`.

**Supporting jobs:**
- `jobs/dev/claude_api_final_review.py` — Claude API review, returns structured JSON, saves to `~/watson/logs/build-reviews/`
- `jobs/dev/build_memory_store.py` — archives full build record after deployment

---

## Dashboard Chat (jobs/dashboard/app.py)

**Primary chat engine:** Claude API (`claude-sonnet-4-6`) with streaming via SSE.
- Loads last 20 messages from `chat_messages` table by session
- Uses full `WATSON_SYSTEM` prompt from `config/settings.py`
- Streams response via `yield _sse(chunk)`
- Saves assistant reply to DB after streaming
- Falls back to Ollama `llama3.2:3b` if `ANTHROPIC_API_KEY` not set or API fails

**Skill intercepts (fire before LLM):** QR generation, Bible lookup, pastoral notes, member lookup, email send, time check, KB search, task add, report menu, skill audit, SMS, identity queries, factual queries.

**Router behavior:** Unrecognized messages fall through to Claude (not skill-build offers).

---

## Skill Registry (memory/skills.json)

Active skills:
| Slug | Name | Triggers |
|---|---|---|
| `time_check` | Time Check | "what time is it", "current time" |
| `dad_joke_skill` | Dad Joke | "tell me a joke" |
| `image_search` | Image Search | "find image", "image of" |
| `add_task` | Add Task | "add a task", "new task", "remind me to" |
| `kb_search` | KB Search | "search kb", "search my notes", "what have i said about" |

**Skill rules:**
- Every skill MUST have `run() -> str` function. Router calls `run()` directly.
- Skills return strings — never call Telegram API directly from a skill.
- Always import `Path` if using `REPO = Path(__file__).resolve().parents[2]`.
- Skills registered in `memory/skills.json` with `interfaces: ["telegram", "dashboard"]`.

---

## Connect Cards Pipeline

**Intake:** Subsplash emails forwarded to `watson.wcky@gmail.com`. `intake.py` polls Gmail API every 30 min, parses HTML with BeautifulSoup (plain text strips form fields). Deduplicates via Gmail Message-ID.

**Parsing:** Campus (Wilmington/Online), name, prayer requests (with `leadership_only` flag), ministry follow-ups/questions/comments, contact info.

**Six next step categories:** `follow_jesus`, `baptism`, `grow_faith`, `catalyst_partner`, `small_group`, `ministry_team`

**Reports:**
- Monday 5am → Dr. Bill: prayer requests (first name + last initial) + ministry follow-ups
- Tuesday 5am → Donna + Kaci: attendance by campus (Wilmington/Online) + first-time visitors
- Kaci's email: `kaci.gravatt@yahoo.com`

**Corrections:** Reply emails from `bill.yomes@gmail.com`, `pastorbill@catalyst302.com`, or `donna@catalyst302.com` accepted. `non-active` keyword marks member inactive.

---

## Pastoral Notes Pipeline

**Jobs:** `jobs/pastoral_notes/prompt.py`, `handler.py`, `reminder.py`

**Flow:** Detects appointments that ended in last 15 min → sends Telegram prompt → Dr. Bill replies → note saved, person fuzzy-matched against congregation.db + people table.

**Skip logic:**
1. `[skip notes]` tag in Google Calendar event title — applied to all repeating events
2. Auto-skip keyword blocklist: `deep work`, `sermon study`, `sabbath`, `family`, `elder`, `staff`, `hair`

**Multi-pending:** When 2+ unanswered prompts exist, sends one consolidated numbered message. Handler parses numbered replies line by line.

**Storage:** `watson.db` — `pastoral_notes` and `notes_pending` tables.

---

## Google Calendar

- **Auth:** OAuth2 — `~/watson/config/credentials.json` + `~/watson/config/token.json`
- **Scope:** Calendar only
- **Calendar ID:** `bill.yomes@gmail.com`
- **Job:** `jobs/gcal/` (renamed from `jobs/calendar/` to avoid Python stdlib conflict)
- **Booking windows:** Wed 10am–1pm, Thu 10am–1pm and 7–8:30pm, Sat 8–9:30am (pastoral only)
- **Public booking:** `williamckyomes.com/meet` — Next.js on Vercel, Google Meet link generation

---

## Email System

- **Gmail:** `watson.wcky@gmail.com`
- **SMTP:** `smtp.gmail.com:587` via Gmail app password
- **Sends as:** `watson@williamckyomes.com` alias
- **Email reply skill:** `jobs/email_reply/` — polls every 15 min, drafts replies via Claude API, sends to Telegram for approval. Dr. Bill replies "send", "change: [text]", or "cancel".
- **Watson signature:** Watson / AI-powered digital assistant / Office of Dr. Bill Yomes / williamckyomes.com/start
- **Outbound from assistant:** Prepend "Dr. Bill asked me to send this to you:"
- **"Send that to me":** Resolves via `WATSON_OWNER_NAME` / `WATSON_OWNER_EMAIL` env vars

---

## Bible API

- **Service:** api.scripture.api.bible
- **Translations:** NIV, CSB, NASB
- **Job:** `jobs/bible.py`
- **Telegram:** `Watson bible John 3:16` (NIV default), `Watson bible CSB Romans 8:28`, `Watson bible all Genesis 1:1`

---

## QR Code Generation

- **Jobs:** `jobs/qr/qr_generate.py` (full), `jobs/utilities/qr_generator.py` (base64 for dashboard)
- **Settings:** `version=None` (auto), `ERROR_CORRECT_M`
- **Regex:** `(?:for[: ]+)?` prefix, capture group `_m.group(1)`
- **Email delivery:** `send_qr_email()` sends as attachment via Watson SMTP

---

## Watson Identity & Email Behavior

- Identity: AI-powered digital assistant — not a bot
- Never pastors, counsels, or speaks with spiritual authority
- Never sends email autonomously — always saves to Drafts or gets explicit approval
- Never extrapolates Dr. Bill's theological positions
- Research hierarchy: primary sources → peer-reviewed → best argument
- Legacy archive: transcripts only, triple-indexed — speaks in Dr. Bill's words only

---

## Dr. Bill's Schedule

- Desk days: Mon–Thu. Watson ONLY schedules Wed/Thu for external bookings.
- Mon: connect cards + sermon study
- Tue: elder 8am, staff 9am (Watson observes only)
- Fri: Sabbath. Sat: family. Sun AM: church. Sun PM: light creative pipeline.
- Deep work: Wed/Thu 9am–2pm in 90-min blocks, 15-min breaks
- People always beat tasks. Tier 1 tasks immovable.

---

## Key Colleagues

- **Donna** — church staff, receives attendance/connect card reports
- **Kaci Gravatt** — `kaci.gravatt@yahoo.com`, receives prayer request reports
- **Mel / Melanie Yomes** — TWJ co-editor
- **Tyler** — student pastor, receives Subsplash emails directly (not Watson workflow)

---

## Development Conventions

- **Workflow:** Claude.ai (strategy/diagnosis/spec) → Claude Code (`--dangerously-skip-permissions`) → Bill pulls and restarts manually
- **Claude Code path:** `/home/billyomes/.nvm/versions/node/v24.16.0/bin/claude`
- **Claude Code never SSHes.** Bill pulls and restarts after every push.
- **After Claude Code commits:** `cd ~/watson && git pull && sudo systemctl restart [service]`
- **sed vs Claude Code:** ≤3 steps use sed. 4+ steps go to Claude Code.
- **Always:** `cd ~/watson && source venv/bin/activate` before running Python
- **Always:** `PYTHONPATH=/home/billyomes/watson` on every cron entry and manual run
- **Always:** `python3` not `python` on Beelink
- **git pull** from `~/watson`, not home directory
- **`jobs/calendar/` renamed to `jobs/gcal/`** — avoids Python stdlib conflict
- **httpx pinned at 0.25.2** — required by python-telegram-bot 20.7, never upgrade
- **Upstash KV double-serialization** — requires `json.loads()` unwrap in `_kv_get`
- **Coding memory:** After every fix, append lesson to `~/watson/memory/coding/python.md` in same commit

---

## WCKY Website

- **Repo:** `github.com/byomes/wcky` | **Beelink path:** `~/wcky`
- **Framework:** Next.js App Router | **Hosting:** Vercel (auto-deploys on push to main)
- **Blog posts:** `content/blog/` — `.md` files named `YYYY-MM-DD-post-slug.md`
- **Publishing schedule:** Tue/Thu/Sat 10am
- **`/twj/read` — DO NOT change this route.** Reader bookmarks depend on it.
- **`/meet`** — public booking page, Google Calendar OAuth via Vercel serverless
- **`/dashboard`** — redirects to `https://watson.tail0243ff.ts.net`
- **For wcky Claude Code prompts:** use full path `/home/billyomes/wcky`, include `git add -A && git commit && git push origin main`

---

## The Wrong Jesus (Book)

- **Status:** Manuscript complete. 14 sections loaded to beta reader site.
- **Beta reader site:** `williamckyomes.com/twj/read` — password-protected, per-chapter feedback
- **Beta reader management:** `watson-admin.vercel.app` (`author.admin`)
- **Press kit:** `williamckyomes.com/twj/press`
- **Co-editor:** Mel

---

## FMS Site

- **Current:** `faithmakessense.com` (builder-made, to be replaced)
- **Planned repo:** `github.com/byomes/fms`
- **Status:** Rebuild plan written, content audited. Build not started.
- **Design:** Intellectual/academic — deep navy or charcoal, serif headlines

---

## Key File Paths

| Location | Path |
|---|---|
| Master credentials | `C:\Users\billy\OneDrive\SECRETS.md` |
| Watson repo (Beelink) | `~/watson/` |
| Dashboard app | `~/watson/jobs/dashboard/app.py` |
| Telegram bot | `~/watson/bot/bot.py` |
| Skill router | `~/watson/jobs/skillbuilder/router.py` |
| Skills registry | `~/watson/memory/skills.json` |
| System prompt | `~/watson/config/settings.py` (`WATSON_SYSTEM`) |
| Watson DB | `~/watson/data/watson.db` |
| Congregation DB | `~/watson/data/congregation.db` |
| KB documents | `~/watson/kb/documents/` (453 files) |
| Build pipeline | `~/watson/jobs/dev/build_pipeline.py` |
| Build memory | `~/watson/memory/builds/` |
| Build index | `~/watson/memory/builds/BUILD_INDEX.md` |
| Coding lessons | `~/watson/memory/coding/python.md` |
| Google OAuth credentials | `~/watson/config/credentials.json` |
| Google OAuth token | `~/watson/config/token.json` |
| Watson JS (dashboard) | `~/watson/jobs/dashboard/static/watson.js` |
| WCKY repo (Beelink) | `~/wcky/` |

---

## Open Items (June 13, 2026)

### Active Bugs
1. `ingest_drafts.py` cron — missing `PYTHONPATH` on that specific entry (global is set but verify)
2. `kb/transcripts` gitignored — transcript archiving falsely reports success, sends broken URL via Telegram
3. `/draft` page UI copy — still says "Pushing to GitHub…", should say "Queuing…"
4. Facebook post structure — excerpt needs 2 sentences max + hashtags
5. `.btn-save` CSS — may still render blue on Vercel briefing

### Resolved Today (June 13, 2026)
- ✅ Full build pipeline live — `build [request]` in Telegram triggers end-to-end autonomous build
- ✅ Claude API as primary chat engine in dashboard (Sonnet streaming, Ollama fallback)
- ✅ First autonomous build deployed — `/api/status` endpoint on dashboard
- ✅ Build memory archive system live — `~/watson/memory/builds/`
- ✅ `claude_api_final_review.py` — structured deployment review via Claude API
- ✅ `build_memory_store.py` — full build record archiving
- ✅ Spec reviewer tuned — blocks real issues only, not style opinions
- ✅ Auto-retry on spec revision — Watson incorporates required changes automatically, no copy-paste
- ✅ Claude Code path fixed — NVM path added to watson-bot systemd service environment
- ✅ File detection via `git diff HEAD~1` — reliable, no output parsing
- ✅ Import-only test — no server startup during local test step

### Planned / Not Yet Built
1. Email reply skill — built, needs end-to-end test
2. Catchall email — `watson@williamckyomes.com` + `watson@faithmakessense.com` at Network Solutions
3. KB search job — `jobs/kb_search.py` — Telegram query → deploys to Vercel → sends link
4. Watson self-improvement system — architecture approved, safety guardrails written
5. Morning briefing auto-push — no manual Telegram command needed
6. FMS site rebuild
7. TWJ provisioning job — bulk credentials + personalized Kit emails to Arc readers
8. Transcription backlog — 10 years of sermon audio on FMSPC
9. Weekly email end-to-end test
10. Book development job — `jobs/book/research_brief.py`
11. People Registry Telegram commands

---

## Upstash KV Namespaces

- `draft:pending:<slug>` — blog draft queue
- `twj:reader:<username>` — TWJ reader credentials
- `twj:readers:index` — all TWJ reader usernames
- `twj:feedback:<chapter>:<username>` — per-chapter reader feedback
- `twj:feedback:all` — all feedback (admin dashboard)
- `watson:saved:<timestamp>` — Save for Later queue items
- `watson:saved:all` — index of saved items
