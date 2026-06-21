# Watson File Map
*Generated: 2026-06-21*
*Excludes: logs/, data/chroma/, kb/documents/, kb/transcripts/, .git/, node_modules/, venv/, __pycache__/, .next/, outputs/, .claude/*

---

## ~/watson/

```
watson/
├── bot/
│   ├── bot.py                        — PRIMARY Telegram bot; commands, intent routing, QR, Writing Room callbacks
│   ├── jenny.py                      ⚠️  ORPHANED: "Jenny" agent persona — retired; no longer called
│   └── __init__.py
│
├── briefing/                         — LEGACY briefing pipeline (predates jobs/dashboard)
│   ├── app.py                        — Old briefing Flask app
│   ├── builder.py                    — Jinja2-based briefing HTML builder
│   ├── publisher.py                  — Push briefing HTML to GitHub + Vercel deploy hook + Telegram
│   └── templates/
│       ├── briefing.html             — Briefing HTML template
│       ├── briefing_static.html
│       ├── dashboard.html
│       ├── library.html
│       ├── reading-list.html
│       ├── research_library.html
│       ├── sources.html
│       └── thought_library.html
│
├── config/
│   ├── credentials.json              — Google OAuth2 client credentials (DO NOT COMMIT)
│   ├── settings.py                   — Central env var loader (dotenv); exports all config constants
│   ├── sources.yaml                  — RSS/content sources for briefing pipeline
│   └── token.json                    — Google OAuth2 access + refresh token (DO NOT COMMIT)
│
├── core/
│   ├── database.py                   — SQLite connection factory using DB_PATH from settings
│   ├── fetcher.py                    — Fetch all active sources up to PER_SOURCE_CAP; archives to research_archive
│   ├── pipeline.py                   — Daily pipeline: fetch → filter → score → store → build → publish
│   ├── scorer.py                     — Two-stage filter/score: freshness + content quality → ranked candidates
│   └── summarizer.py                 — Text summarizer (TF-IDF / extractive)
│
├── cron/
│   └── run_pipeline.sh               — Shell wrapper to run core/pipeline.py with correct env
│
├── data/
│   ├── congregation.db               — Pastoral CRM: members, attendance, connect cards, prayer, follow-ups
│   ├── donors.db                     — Givebutter donor and transaction records
│   ├── exports/                      — QR code PNGs generated via /export command (older naming)
│   ├── qr/                           — QR code PNGs from jobs/qr/qr_generate.py (current)
│   ├── riddle_history.json           — Tracks which riddles have been sent to avoid repeats
│   ├── skill_audit.json              — Output of jobs/skillbuilder/audit.py skill health check
│   └── watson.db                     — PRIMARY DB: tasks, reminders, chat, blog, facebook, connect cards, writing room
│
├── deploy/
│   ├── connect_cards_cron.txt        — Legacy cron reference for connect cards jobs
│   ├── index.html                    — Static deploy placeholder
│   ├── people-server.service         — systemd unit for jobs/people/server.py (port 5100)
│   ├── start_people_server.sh        — Shell launcher for people server
│   └── watson-dashboard.service      — systemd unit for jobs/dashboard/app.py (port 5200; ACTIVE)
│
├── docs/
│   └── briefing.html                 — Last-published briefing HTML (written by briefing/publisher.py)
│
├── jobs/
│   │
│   ├── acquired/                     ⚠️  UNCLEAR PURPOSE — no docstrings
│   │   ├── chump.py                  — Imports sqlite3, requests, dotenv; purpose undocumented
│   │   └── send.py                   — Imports requests, datetime; purpose undocumented
│   │
│   ├── ask.py                        — KB semantic search via ChromaDB (query → relevant chunks)
│   ├── batch.py                      — Batch audio transcription for KB backlog; resume-safe
│   ├── bible.py                      — Bible passage lookup via api.scripture.api.bible (NIV/CSB/NASB)
│   ├── build_kb.py                   — Build/rebuild ChromaDB vector index from kb/documents/
│   ├── cleanup.py                    — Pass-through: copies raw Whisper transcript to clean dir unchanged
│   ├── email_intake.py               — Cron: poll Gmail IMAP, classify with Ollama, Telegram alert for urgent
│   ├── generate.py                   — Archive clean transcript to kb/transcripts/ + push to GitHub
│   ├── ingest_drafts.py              — Cron */15: poll Upstash KV for draft:pending:* → insert into watson.db
│   ├── memory_manager.py             — Read/write Watson memory flat files (memory/)
│   ├── note.py                       — Note creation/append for sermon series or any topic
│   ├── reading_list.py               — Reading list manager backed by ~/watson/data/reading_list.json
│   ├── scheduler.py                  — Cron daily: publish scheduled blog drafts to byomes/wcky via GitHub API
│   ├── time_check.py                 — Date/time utility (ZoneInfo-based; used by other jobs)
│   ├── transcribe.py                 — Whisper transcription wrapper (weekly + archive modes)
│   └── watcher.py                    — PC-only: watches INCOMING_DIR + ARCHIVE_DIR, triggers pipeline
│   │
│   ├── briefing/                     ⚠️  Uses google.generativeai (GEMINI — RETIRED)
│   │   ├── gemini_narrative.py       — Gemini narrative generation for briefing (RETIRED)
│   │   └── gemini_relevance.py       — Gemini relevance scoring for briefing (RETIRED)
│   │
│   ├── code_agent/
│   │   ├── agent.py                  — Claude Code agent launcher (sqlite3-based job tracking)
│   │   ├── confirm.py                — Approval gate: Telegram confirm before code agent runs
│   │   └── prompts/build.md          — Prompt template for code agent build tasks
│   │
│   ├── congregation/                 — One-time + utility scripts for congregation.db
│   │   ├── batch_intake.py           — CSV batch import from Subsplash export into congregation.db
│   │   ├── init_db.py                — Initialize congregation.db schema
│   │   ├── member_match.py           — Priority-ordered member matching: email → phone → name fuzzy
│   │   └── migrate_reparse.py        — Re-parse already-processed connect card emails to backfill DB
│   │
│   ├── connect_cards/                — LIVE: Do NOT modify
│   │   ├── attendance_intake.py      — Cron */30: parse attendance emails from Gmail
│   │   ├── backfill.py               — One-time backfill script for attendance records
│   │   ├── correction_handler.py     — Cron */30: process attendance correction emails
│   │   ├── data_audit.py             — Audit congregation.db for data integrity issues
│   │   ├── email_reports.py          — Cron: weekly summaries to Bill (Mon 5am), Donna+Kaci (Tue 5am)
│   │   ├── intake.py                 — Cron */30: IMAP poll for connect card emails → congregation.db
│   │   ├── migrate_prayer_leadership.py — One-time: migrate prayer/leadership fields
│   │   ├── missed_report.py          — Cron Mon 6am: members absent 2+ Sundays alert
│   │   ├── pastoral_reports.py       — Report generation helpers
│   │   ├── report_menu.py            — Menu/selector for report types
│   │   ├── reports.py                — Core report formatting
│   │   ├── shepherding_report.py     — Cron Wed 6am: absent 3+wks, next steps, prayer requests digest
│   │   └── utils.py                  — Shared parsing utilities
│   │
│   ├── contacts/
│   │   └── vcf_importer.py           — Import .vcf vCard contacts into watson.db people table
│   │
│   ├── dadjoke/
│   │   └── joke.py                   — Random dad joke skill; avoids repeats via riddle_history.json
│   │
│   ├── dashboard/
│   │   ├── app.py                    — PRIMARY Flask app (port 5200); chat SSE, QR, Writing Room blueprint
│   │   ├── app.py.bak                — Backup of dashboard app before last major edit
│   │   ├── migrate_sessions.py       — One-time: migrate chat_sessions table schema
│   │   ├── static/
│   │   │   ├── favicon.svg
│   │   │   ├── style.css             — Dashboard UI styles
│   │   │   └── watson.js             — Dashboard JS: SSE chat, QR rendering, tab switching
│   │   └── templates/
│   │       └── index.html            — Dashboard HTML (single-page; tabs: Home/Briefing/Tasks/Reminders/Reading/More)
│   │
│   ├── data/
│   │   ├── chart_generator.py        — Generate charts from DB data (matplotlib)
│   │   ├── data_analyzer.py          — Statistical analysis helpers
│   │   └── table_extractor.py        — Extract tabular data from text
│   │
│   ├── design/
│   │   ├── image_tools.py            — Resize, watermark, optimize, convert images
│   │   ├── screenshot.py             — Webpage screenshots via Playwright
│   │   └── svg_generator.py          — Branded banners, quote cards, social graphics (SVG/PNG)
│   │
│   ├── dev/
│   │   ├── auto_fixer.py             — Auto-fix common skill errors (AST-based)
│   │   ├── build_memory_store.py     — Build/refresh Watson memory store from flat files
│   │   ├── build_pipeline.py         — End-to-end Watson build pipeline orchestrator
│   │   ├── claude_api_final_review.py — Claude API call for final code review step
│   │   ├── claude_debug.py           — Debug loop: diagnose → fix → review → notify via Telegram
│   │   ├── code_agent.py             — Claude Code agent launcher (dev/jobs variant)
│   │   ├── code_analyzer.py          — Static analysis of Watson job files
│   │   ├── code_editor.py            — File editor helper for dev jobs
│   │   ├── code_quality.py           — Code quality checks (complexity, style)
│   │   ├── command_executor.py       — Safe subprocess executor with timeout
│   │   ├── dependency_manager.py     — pip dependency management helper
│   │   ├── dependency_scanner.py     — Scan jobs/ for import statements → dependency graph
│   │   ├── error_analyzer.py         — Parse error logs and suggest fixes
│   │   ├── gemini_coder_test.py      ⚠️  Gemini test (RETIRED — do not use)
│   │   ├── git_tools.py              — git history, diff, manual fix detection helpers
│   │   ├── github_tools.py           — GitHub repo interaction via PyGithub
│   │   ├── hello_dashboard.py        — Minimal dashboard smoke test (prints "Hello from dashboard")
│   │   ├── performance_profiler.py   — CPU/memory profiling for Watson jobs
│   │   ├── secrets_audit.py          — Scan jobs/ for env var references vs .env contents
│   │   ├── skill_tester.py           — Manual skill runner for testing
│   │   ├── skill_validator.py        — Validate skill JSON entries against actual job files
│   │   ├── system_monitor.py         — System health: CPU, memory, disk, service status
│   │   ├── test_gemini.py            ⚠️  Gemini integration test (RETIRED — do not use)
│   │   └── test_runner.py            — Run Watson job unit tests
│   │
│   ├── documents/
│   │   ├── excel.py                  — Read/create .xlsx files
│   │   ├── pdf.py                    — Read/create PDF files
│   │   ├── powerpoint.py             — Read/create .pptx files
│   │   └── word.py                   — Read/create .docx files
│   │
│   ├── email/                        ⚠️  EMPTY GHOST DIRECTORY (only __pycache__; no .py files)
│   │
│   ├── email_job/
│   │   ├── draft_email.py            — Cron Thu 7am: pull queued articles, draft newsletter, create Kit broadcast
│   │   ├── email_queue.py            — SQLite queue for outbound emails
│   │   ├── gmail.py                  — Gmail SMTP send helper (MIMEMultipart, starttls)
│   │   └── __init__.py
│   │
│   ├── email_reply/
│   │   ├── drafter.py                — Draft reply text via Ollama qwen2.5:7b for email approval
│   │   ├── handler.py                — DB persistence, Telegram notification, SMTP reply sender
│   │   ├── reader.py                 — Cron */15: IMAP poll → draft reply → Telegram approval → mark SEEN
│   │   └── __init__.py
│   │
│   ├── email_send/
│   │   ├── send.py                   — Generic outbound email sender (wraps gmail.py)
│   │   └── __init__.py
│   │
│   ├── facebook/
│   │   ├── facebook_post.py          — Cron */15: dequeue facebook_queue and post to Facebook via Graph API
│   │   ├── scheduler.py              — Queue checker: find due posts in facebook_queue
│   │   └── templates.py              — Format article content → Facebook post text (2-sentence excerpt + hashtags)
│   │
│   ├── gcal/
│   │   ├── availability.py           — Query Google Calendar for open booking windows
│   │   ├── create_event.py           — Create calendar events via Google Calendar API
│   │   ├── gcal_service.py           — Google Calendar API client (OAuth2 token refresh)
│   │   ├── notify.py                 — Send calendar event notifications via Telegram
│   │   ├── pending.py                — Check pending calendar actions from tg_pending_actions
│   │   ├── pre_meeting_brief.py      — Cron */5: Telegram brief 25-35 min before VA:/IP: appointments
│   │   ├── reauth.py                 — Interactive Google OAuth2 re-authentication flow
│   │   ├── reasoner.py               — Ollama-based scheduling reasoner (parse natural language → time slot)
│   │   └── token_health.py           — Cron daily 7am: verify OAuth token validity; alert if expired
│   │
│   ├── givebutter/
│   │   ├── notify.py                 — Cron daily 6:15am: find unthanked transactions → Telegram preview with inline keyboard
│   │   ├── sync.py                   — Cron daily 6am: Givebutter API → donors.db → Kit subscriber sync
│   │   └── templates.py              — Email templates for first-gift and recurring donor thank-yous
│   │
│   ├── intent/
│   │   └── classifier.py             — Ollama llama3.2:3b intent classification for Telegram messages
│   │
│   ├── kb/
│   │   └── archive_transcripts.py    — Cron daily 2am: move old transcripts from kb/transcripts/ → kb/documents/; git commit+push
│   │
│   ├── marketing/                    ⚠️  Not wired to any active cron or skill
│   │   ├── content_calendar.py       — Content calendar from DB drafts + AI suggestions
│   │   ├── seo_tools.py              — SEO page analysis, sitemap generation, keyword suggestions
│   │   └── social_poster.py          — Social media posting helper
│   │
│   ├── media/
│   │   ├── audio_tools.py            — Audio metadata, format conversion, trimming
│   │   └── youtube_downloader.py     — Download audio from YouTube via yt-dlp
│   │
│   ├── memory/
│   │   ├── new_project.py            — Create a new project memory directory + stub .md file
│   │   ├── propose.py                — Propose and resolve Watson memory updates (Telegram-driven)
│   │   ├── reflect.py                — Automatic post-session reflection; writes to memory/
│   │   ├── sync.py                   — Sync memory flat files → watson.db memory tables
│   │   └── wrap_up.py                — Manual session wrap-up; save memory + commit
│   │
│   ├── misc/                         ⚠️  Grab-bag; mostly orphaned utility stubs
│   │   ├── both_read_pdf.py          — PDF reader utility (sqlite3-based)
│   │   ├── here_link_book.py         — Undocumented; purpose unclear
│   │   ├── im_trying_file.py         — Undocumented test file
│   │   ├── riddle.py                 — Random riddle skill (uses API; avoids repeats)
│   │   ├── tells_many_days.py        — Days-until-Christmas countdown (named jobs/christmas_count in docstring)
│   │   └── update_your_own.py        — Undocumented
│   │
│   ├── monitoring/
│   │   ├── adds_memory_files.py      ⚠️  Uses python-telegram-bot v13 Updater API (incompatible with v20.7)
│   │   ├── log_watch.py              — Watch Watson log files for error patterns; Telegram alert
│   │   └── weather_every_morning.py  — Daily 6am weather forecast via Telegram (asyncio)
│   │
│   ├── pastoral_notes/
│   │   ├── db.py                     — SQLite helpers for notes_pending table
│   │   ├── handler.py                — Handle Telegram reply → match pending note → store pastoral record
│   │   ├── prompt.py                 — Cron */5: query GCal for appointments ended <15min → Telegram prompt
│   │   └── reminder.py               — Cron */15: follow-up reminders for unanswered notes_pending rows
│   │
│   ├── people/                       — LIVE: Do NOT modify
│   │   ├── api.py                    — Callable module: people_list, congregation_search, etc.
│   │   ├── google_contacts.py        — Sync Google Contacts → watson.db people table
│   │   ├── lookup.py                 — Search congregation.db then watson.db by name/email/phone
│   │   ├── migrate.py                — One-time people table migration
│   │   ├── registry.py               — People Registry: watson.db people table CRUD
│   │   └── server.py                 — Stdlib HTTP API on port 5100; no external deps
│   │
│   ├── qr/
│   │   ├── qr_generate.py            — generate_qr(content) → (filepath, png_bytes); email + Telegram delivery
│   │   └── __init__.py
│   │
│   ├── reminders/
│   │   ├── check_reminders.py        — Cron * (every min): check watson.db for due reminders → Telegram
│   │   ├── check_timed.py            — Cron */5: check timed reminders with exact timestamps
│   │   └── daily_summary.py          — Cron 10am/1:30pm/5pm (Mon-Sat): send daily task+reminder summary
│   │
│   ├── research/
│   │   ├── academic_search.py        — Academic paper search (Semantic Scholar / arXiv)
│   │   ├── article_reader.py         — Fetch full text of articles and web pages (requests + BeautifulSoup)
│   │   ├── feed_reader.py            — Parse RSS/Atom feeds → latest entries
│   │   ├── gemini_fetch.py           ⚠️  Gemini-based web fetch (RETIRED)
│   │   ├── isbn_lookup.py            — Book metadata lookup by ISBN or title
│   │   ├── language_detector.py      — Detect language of text (langdetect)
│   │   ├── news_search.py            — News search via Serper.dev
│   │   ├── semantic_search.py        — Semantic similarity search over Watson memory markdown files
│   │   ├── summarizer.py             — Article summarization via Ollama
│   │   └── web_search.py             — Web search via Serper.dev (Google results)
│   │
│   ├── security/
│   │   └── encryptor.py              — File/text encryption helpers (Fernet)
│   │
│   ├── skillbuilder/
│   │   ├── acquire.py                — Acquire new skills from Telegram input → add to skills.json
│   │   ├── audit.py                  — Audit all skills in skills.json; test + write skill_audit.json
│   │   ├── build.py                  — Three-tier skill builder: Ollama → Claude Sonnet → Claude Code
│   │   ├── research.py               — Research background for new skill before building
│   │   └── router.py                 — PRIMARY: route Telegram messages to skill modules or Ollama chat
│   │
│   ├── skills/
│   │   ├── book_appointment.py       — Book calendar appointments from Telegram NL commands
│   │   ├── contacts_lookup.py        — Look up people from watson.db contacts
│   │   ├── kb_export.py              — Export KB search results to file
│   │   ├── kb_search.py              — Search kb/documents/ for query terms (file-based)
│   │   ├── pastoral_search.py        — Pastoral summary for a named member (congregation.db)
│   │   └── __init__.py
│   │
│   ├── sms/
│   │   └── sms_send.py               — Send SMS via SMTP-to-SMS gateway
│   │
│   ├── social/                       ⚠️  Skeletal; jobs/facebook/ handles active social posting
│   │   └── image_search.py           — Image search helper (no docstring)
│   │
│   ├── tasks/
│   │   └── add_task.py               — Add task from NL message; parse title, due date, priority → watson.db
│   │
│   ├── telegram/
│   │   ├── pending.py                — Reply-threading: track pending actions keyed by Telegram message ID
│   │   └── resend_last.py            — Resend Watson's last Telegram message
│   │
│   ├── utilities/
│   │   ├── calendar_importer.py      — Import .ics calendar files into Google Calendar
│   │   ├── date_helper.py            — Date parsing and formatting utilities
│   │   ├── qr_generator.py           ⚠️  DUPLICATE of jobs/qr/qr_generate.py; older version
│   │   ├── template_engine.py        — Jinja2 template rendering helper
│   │   └── text_processor.py         — Text cleaning, tokenization, chunking utilities
│   │
│   ├── web/
│   │   ├── page_generator.py         — Generate static HTML pages from templates
│   │   └── site_deployer.py          — Deploy static sites to GitHub Pages or Vercel
│   │
│   ├── writing/
│   │   ├── citation_manager.py       — Citation formatting (APA/MLA/Chicago)
│   │   ├── document_converter.py     — Convert between document formats (docx/pdf/md)
│   │   ├── epub_generator.py         — Generate .epub from markdown content
│   │   ├── grammar_checker.py        — Grammar checking via LanguageTool
│   │   ├── manuscript_tracker.py     — Track manuscript word count, sections, revision history
│   │   ├── readability.py            — Flesch-Kincaid and other readability scores
│   │   ├── spell_checker.py          — Spell checking helpers
│   │   ├── style_checker.py          — Style guide compliance checking
│   │   └── wordcloud_generator.py    — Word cloud generation from text
│   │
│   └── writing_room/
│       ├── __init__.py               — Shared: bootstrap_db(), send_telegram(), send_email(), generate_username/password()
│       ├── api.py                    — Flask Blueprint (10 routes) registered on dashboard; Writing Room API
│       ├── monitor.py                — Cron */5: poll Writing Room tables → Telegram alerts for new activity
│       ├── onboard.py                — alert_new_application(), process_approval(), process_denial(), welcome email, Kit tag
│       ├── remind.py                 — Cron */15: 24h and 1h call reminders to all active partners
│       └── reset.py                  — Token-based password reset: request, validate, confirm
│
├── kb/
│   ├── bulk_ingest.py                ⚠️  References OpenWebUI (RETIRED); may be orphaned
│   ├── cleanup_collections.py        ⚠️  References OpenWebUI (RETIRED); may be orphaned
│   └── watcher.py                    ⚠️  References Windows paths (F:\Knowledge_Database); PC-only, not Beelink
│
├── library/
│   ├── ingestor.py                   — Document ingestor into core knowledge library (Jinja2-based)
│   └── search.py                     — Search library via core.database connection
│
├── memory/
│   ├── architecture.md               — Legacy architecture notes (superseded by WATSON_ARCHITECTURE.md)
│   ├── builds/                       — Build session logs (spec, diff, review, approval, deployment)
│   │   └── BUILD_INDEX.md            — Index of all recorded build sessions
│   ├── coding/                       — Coding reference cards
│   │   ├── _index.md
│   │   ├── nextjs.md
│   │   ├── ollama.md
│   │   ├── python.md
│   │   ├── sqlite.md
│   │   └── telegram.md
│   ├── core.md                       — Core Watson identity and constraint notes
│   ├── CRON.md                       — Active cron job registry
│   ├── FILE_MAP.md                   — THIS FILE: full repo file map (regenerated weekly)
│   ├── projects/                     — Per-project memory directories (Godfidence, Joshua series, etc.)
│   ├── relational.md                 — Relationship/people context notes
│   ├── skills.json                   ⚠️  References jobs.calendar.clear_day (should be jobs.gcal.clear_day)
│   ├── skip_keywords.txt             — Keywords that skip Ollama intent classification
│   └── WATSON_ARCHITECTURE.md        — PRIMARY architecture reference (read before any build)
│
├── prompts/
│   ├── cleanup.md                    — Prompt for transcript cleanup job
│   ├── generate_blog.md              — Prompt for blog post generation from transcript
│   └── generate_social.md            — Prompt for social media seed generation
│
├── web/                              ⚠️  OLD Next.js review app for blog draft approval (pre-dashboard)
│   ├── package.json
│   └── pages/
│       ├── index.jsx                 — Blog draft review page (reads from Vercel KV)
│       ├── social.jsx                — Social seeds review page
│       └── api/
│           ├── approve-blog.js       — Push approved .md to byomes/wcky via GitHub API
│           ├── approve-social.js     — Write social seeds to Vercel KV queue
│           └── get-draft.js          — Read sermon:current from Vercel KV
│
├── .env                              — Runtime secrets (DO NOT COMMIT)
├── .env.example                      — Template for required env vars
├── .env.local                        — Local overrides (DO NOT COMMIT)
├── .gitignore
├── CLAUDE.md                         — Legacy CLAUDE.md (outdated; use WATSON_ARCHITECTURE.md)
├── cron_additions.txt                — Cron lines to add via crontab -e
├── main.py                           — Entry point stub (minimal)
├── README.md
├── requirements.txt                  — Python dependencies
├── run.sh                            — Shell launcher
├── vercel.json                       ⚠️  Belongs to web/ subdirectory (old review app); should not be at root
└── watson.db                         ⚠️  Zero-byte stray file; real DB is at data/watson.db

── ROOT-LEVEL STRAY FILES ──────────────────────────────────────────────────────
  '                                   ⚠️  File named with a single quote; likely shell accident
  Catalyst Connect Cards 06-08-2026.csv ⚠️  Raw data CSV in repo root; should not be committed
  contacts.csv                        ⚠️  Raw contacts export in repo root; should not be committed
  google-chrome-stable_current_amd64.deb ⚠️  Binary installer in repo root; should be deleted/gitignored
  import_connect_cards.py             — One-time CSV import script (root-level; candidate for move to jobs/)
  import_contacts.py                  — One-time contacts import script (root-level)
  jobs.skillbuilder.theology_apologetics_testing.py ⚠️  MISNAMED: dots instead of slashes; should be jobs/skillbuilder/
```

---

## ~/wcky/

```
wcky/
├── content/
│   └── blog/                         — Published blog posts (markdown with frontmatter; Tue/Thu/Sat 10am)
│       └── [25 posts, 2026-04-21 through 2026-06-21]
│
├── posts/                            ⚠️  OLD MDX posts directory; predates content/blog/; likely unused
│   ├── faith-and-reason.mdx
│   ├── welcome-to-my-blog.mdx
│   └── why-every-christian-should-know-apologetics.mdx
│
├── public/
│   ├── images/                       — Static image assets (headshots, book covers, OG images, lead magnets)
│   └── posts/williamckyomes.WordPress.2026-05-05.xml — WordPress export archive (stray; not used by app)
│
├── scripts/
│   └── generate-og-meet.py           — One-time script to generate OG image for /meet page
│
├── src/
│   ├── app/
│   │   ├── layout.tsx                — Root layout (fonts, metadata, Header, Footer)
│   │   ├── page.tsx                  — Homepage
│   │   ├── globals.css               — Global styles
│   │   ├── not-found.tsx             — 404 page
│   │   │
│   │   ├── about/page.tsx            — About page
│   │   ├── arc/page.tsx              — ARC reader sign-up / interest page
│   │   ├── blog/
│   │   │   ├── page.tsx              — Blog index (lists content/blog/ posts)
│   │   │   └── [slug]/page.tsx       — Individual blog post page
│   │   ├── books/page.tsx            — Books page (TWJ + Dreamstone)
│   │   ├── cv/
│   │   │   ├── page.tsx              — CV / résumé page
│   │   │   ├── cv.css                — CV-specific styles
│   │   │   └── CvDownloadButton.tsx  — Client component: download CV PDF button
│   │   ├── dashboard/page.tsx        — Redirect → https://watson.tail0243ff.ts.net
│   │   ├── draft/page.tsx            — Blog draft submission form → Upstash KV
│   │   ├── dreamstone/page.tsx       — Dreamstone series page
│   │   ├── ingest/page.tsx           — Internal content ingest UI
│   │   ├── meet/
│   │   │   ├── page.tsx              — Public booking page (server component)
│   │   │   ├── MeetClient.tsx        — Client component: booking form, availability calendar
│   │   │   └── cancel/page.tsx       — Booking cancellation page
│   │   ├── read/[slug]/              ⚠️  APPEARS DUPLICATE of /twj/read — dynamic slug reader (may be legacy)
│   │   │   ├── page.tsx              — Reader page with same structure as twj/read
│   │   │   ├── LoginForm.tsx         — Login form (duplicate of twj/read/LoginForm.tsx)
│   │   │   └── ManuscriptReader.tsx  — Manuscript reader (duplicate of twj/read/ManuscriptReader.tsx)
│   │   ├── speaking/page.tsx         — Speaking page
│   │   ├── start/page.tsx            — Start/welcome page
│   │   ├── theology/page.tsx         — Theology page
│   │   ├── twj/
│   │   │   ├── page.tsx              — TWJ landing page
│   │   │   ├── press/page.tsx        — TWJ press kit
│   │   │   └── read/                 — CANONICAL TWJ reader (DO NOT CHANGE ROUTE)
│   │   │       ├── page.tsx          — Protected reader page (cookie auth)
│   │   │       ├── LoginForm.tsx     — TWJ login form
│   │   │       ├── ManuscriptReader.tsx — Copy-protected manuscript reader (no right-click, Ctrl+C blocked)
│   │   │       └── chapters/         — 14 markdown chapter files (introduction, ch01-12, conclusion)
│   │   │
│   │   └── room/                     — Writing Room (private partner community)
│   │       ├── page.tsx              — Public: apply form or redirect to board if logged in
│   │       ├── ApplyForm.tsx         — Application form client component (name, email, why_join, faith_description, checkbox)
│   │       ├── login/page.tsx        — Partner login page
│   │       ├── reset/page.tsx        — Password reset flow (request → validate → confirm)
│   │       ├── admin/
│   │       │   ├── page.tsx          — Admin view: partners, pending, messages, calls (server component)
│   │       │   └── login/page.tsx    — Admin login page
│   │       └── (protected)/          — Requires writing_room_session cookie
│   │           ├── layout.tsx        — Protected layout: top bar + bottom nav
│   │           ├── PostList.tsx      — Shared client component: posts with inline reply forms
│   │           ├── RoomNav.tsx       — Bottom navigation component
│   │           ├── board/page.tsx    — Community board (general posts)
│   │           ├── beta/
│   │           │   ├── page.tsx      — Beta drafts listing page (server component)
│   │           │   └── BetaDraftList.tsx — Client: expand draft, react, comment
│   │           ├── calls/page.tsx    — Upcoming Author Calls listing
│   │           ├── prayer/page.tsx   — Prayer wall
│   │           └── write/page.tsx    — Write to Dr. Bill (direct message form)
│   │
│   ├── app/api/
│   │   ├── ingest/route.ts           — Content ingest endpoint
│   │   ├── meet/
│   │   │   ├── availability/route.ts — Fetch available booking slots from Watson GCal
│   │   │   └── book/route.ts         — Create booking → Watson GCal
│   │   ├── read/[slug]/              — TWJ reader API routes
│   │   │   ├── feedback/route.ts     — Submit chapter feedback → Upstash KV
│   │   │   ├── login/route.ts        — TWJ reader login (Upstash KV credential check)
│   │   │   └── logout/route.ts       — Clear TWJ reader session cookie
│   │   ├── room/
│   │   │   ├── apply/route.ts        — Validate + forward application to Watson /api/writing-room/signup
│   │   │   ├── feedback/route.ts     — Forward beta feedback to Watson
│   │   │   ├── login/route.ts        — Validate credentials → set writing_room_session cookie
│   │   │   ├── logout/route.ts       — Clear writing_room_session cookie
│   │   │   ├── message/route.ts      — Forward "Write to Dr. Bill" messages to Watson
│   │   │   ├── post/route.ts         — Forward board/prayer posts to Watson
│   │   │   ├── reset/route.ts        — Password reset proxy (request/validate/confirm)
│   │   │   └── admin/login/route.ts  — Admin login (bcrypt check against env vars)
│   │   ├── submit-draft/route.ts     — Blog draft submission → Upstash KV
│   │   └── twj/
│   │       ├── feedback/route.ts     — TWJ feedback (same as read/[slug]/feedback; newer route)
│   │       ├── login/route.ts        — TWJ login (newer route)
│   │       └── logout/route.ts       — TWJ logout (newer route)
│   │
│   ├── components/
│   │   ├── Footer.tsx                — Site footer
│   │   ├── FreeResourceButton.tsx    — CTA button for free resource download
│   │   ├── Header.tsx                — Site header / nav
│   │   ├── HeroButtons.tsx           — Hero section CTA buttons
│   │   ├── HomePopup.tsx             — Homepage popup/modal component
│   │   ├── LeadMagnetModal.tsx       — Lead magnet download modal
│   │   └── StartCTA.tsx              — Start CTA section component
│   │
│   ├── content/
│   │   └── books/twj/beta/           — TWJ beta draft markdown files for Writing Room /beta section
│   │       └── sample-draft.md       — Sample beta draft placeholder
│   │
│   ├── lib/
│   │   ├── posts.ts                  — Blog post file reader (parses content/blog/ markdown with frontmatter)
│   │   ├── writing-room-api.ts       — Watson API client (server-side; all Writing Room API calls)
│   │   └── writing-room-auth.ts      — HMAC-signed cookie auth (Web Crypto; Edge Runtime compatible)
│   │
│   ├── middleware.ts                 — Protects /room/* routes; verifies HMAC session cookies
│   └── types/index.ts                — Shared TypeScript type definitions
│
├── next.config.js
├── package.json
├── postcss.config.js
├── tailwind.config.ts
├── tree.txt                          ⚠️  Stray file: Windows path tree snapshot from OneDrive; not useful
└── tsconfig.json
```

---

## ~/watson-admin/

```
watson-admin/                         — Book/reader management admin (watson-admin.vercel.app)
│
├── app/
│   ├── layout.tsx                    — Root layout
│   ├── globals.css
│   ├── favicon.ico
│   ├── login/page.tsx                — Admin login form (bcrypt password check)
│   └── (admin)/                      — Requires admin_session cookie
│       ├── page.tsx                  — Admin dashboard home
│       └── books/
│           ├── page.tsx              — Books index
│           └── twj/page.tsx          — TWJ reader management (list readers, add, reset password)
│
├── app/api/
│   ├── auth/
│   │   ├── login/route.ts            — POST: bcrypt check, set admin_session cookie
│   │   └── logout/route.ts           — DELETE admin_session cookie
│   └── books/
│       ├── route.ts                  — GET/POST books list (Upstash KV)
│       ├── [slug]/route.ts           — GET/PUT/DELETE individual book (Upstash KV)
│       └── twj/
│           ├── feedback/
│           │   ├── route.ts          — GET TWJ chapter feedback (Upstash KV)
│           │   └── delete/route.ts   — DELETE feedback entry
│           └── readers/
│               ├── route.ts          — GET list of TWJ readers; POST create new reader
│               ├── bulk/route.ts     — POST bulk-create readers (CSV/JSON)
│               └── [username]/
│                   ├── route.ts      — GET/DELETE individual reader
│                   └── reset-password/route.ts — POST reset reader password
│
├── components/
│   ├── AdminShell.tsx                — Top-level admin shell: Sidebar + TopBar wrapper
│   ├── Sidebar.tsx                   — Admin sidebar navigation
│   ├── SidebarContext.tsx            — Sidebar open/close state context
│   └── TopBar.tsx                    — Admin top bar (page title, logout)
│
├── lib/
│   ├── auth.ts                       — getSessionUsername() from admin_session cookie
│   └── kv.ts                         — Upstash Redis client singleton
│
├── scripts/
│   └── hash-password.js              — CLI: bcrypt hash a password for ADMIN_PASSWORD env var
│
├── proxy.ts                          — Next.js middleware: guard (admin) routes, redirect to /login
├── AGENTS.md                         — Agent/build instructions for this repo
├── CLAUDE.md                         — Claude Code instructions for this repo
├── next.config.ts
├── package.json
└── tsconfig.json
```

---

## ~/watson-ui/

```
watson-ui/                            — Alternative Watson web interface (deprioritized; see architecture)
│
├── app/
│   ├── layout.tsx                    — Root layout
│   ├── globals.css
│   ├── favicon.ico
│   └── page.tsx                      — Main page: login gate → tabbed view (Briefing/Tasks/Contacts/Reading/Settings)
│
├── app/api/
│   ├── auth/
│   │   ├── route.ts                  — POST login (set watson_session cookie)
│   │   └── check/route.ts            — GET session validation
│   ├── chat/route.ts                 — POST: proxy message to Watson dashboard chat endpoint
│   ├── congregation/
│   │   ├── route.ts                  — GET congregation member list from Watson API
│   │   └── [id]/route.ts             — GET individual member record
│   ├── logout/route.ts               — DELETE watson_session cookie
│   └── people/
│       ├── route.ts                  — GET people list from Watson API
│       └── [id]/route.ts             — GET individual person record
│
├── components/
│   ├── BriefingView.tsx              — Briefing tab: fetch and display daily briefing via Watson API
│   ├── ContactsView.tsx              — Contacts tab: search congregation/people registry
│   ├── LoginScreen.tsx               — Login form component
│   ├── ReadingView.tsx               — Reading list tab
│   ├── SettingsView.tsx              — Settings tab
│   └── TasksView.tsx                 — Tasks tab: list, add, complete tasks
│
├── public/
│   └── manifest.json                 — PWA web app manifest
│
├── AGENTS.md                         — Agent/build instructions for this repo
├── CLAUDE.md                         — Claude Code instructions for this repo
├── .env.example                      — Required env vars template
├── next.config.ts
├── package.json
└── tsconfig.json
```

---

## Flags

### 🔴 Should be cleaned up

| File/Dir | Issue |
|----------|-------|
| `watson/'` | File named with a literal single-quote — shell accident; safe to delete |
| `watson/google-chrome-stable_current_amd64.deb` | Binary installer in repo root; should be deleted and gitignored |
| `watson/watson.db` | Zero-byte stray file; real DB is `data/watson.db`; delete or gitignore |
| `watson/Catalyst Connect Cards 06-08-2026.csv` | Raw data CSV committed to repo root; should not be in git |
| `watson/contacts.csv` | Raw contacts export at repo root; should not be in git |
| `watson/jobs/email/` | Empty ghost directory — only `__pycache__` exists, no `.py` files; safe to remove |
| `watson/jobs.skillbuilder.theology_apologetics_testing.py` | Misnamed: uses dots as path separators; should be `jobs/skillbuilder/theology_apologetics_testing.py` or deleted |
| `wcky/tree.txt` | Stray Windows path tree snapshot from OneDrive; not used by app |
| `wcky/posts/` | Old MDX posts directory predating `content/blog/`; unused in current pipeline |

### 🟡 Orphaned or retired code (review before deleting)

| File/Dir | Issue |
|----------|-------|
| `watson/bot/jenny.py` | "Jenny" agent persona — retired per architecture; no longer called by bot.py |
| `watson/jobs/briefing/gemini_narrative.py` | Uses `google.generativeai` (Gemini) — permanently retired; Ollama only |
| `watson/jobs/briefing/gemini_relevance.py` | Same — Gemini retired |
| `watson/jobs/research/gemini_fetch.py` | Gemini-based web fetch — retired |
| `watson/jobs/dev/gemini_coder_test.py` | Gemini coder test — retired |
| `watson/jobs/dev/test_gemini.py` | Gemini integration test — retired |
| `watson/jobs/utilities/qr_generator.py` | Older QR generator; active version is `jobs/qr/qr_generate.py` |
| `watson/jobs/monitoring/adds_memory_files.py` | Uses python-telegram-bot v13 `Updater` API; incompatible with v20.7 |
| `watson/kb/bulk_ingest.py` | References OpenWebUI (retired); likely orphaned |
| `watson/kb/cleanup_collections.py` | References OpenWebUI (retired); likely orphaned |
| `watson/kb/watcher.py` | Windows-path KB watcher; PC-only, not relevant on Beelink |
| `watson/web/` | Old Next.js blog-draft review app (pre-dashboard); superseded by scheduler.py + Watson flow |
| `watson/briefing/` | Top-level legacy briefing module; predates `jobs/dashboard/`; relationship to active code unclear |
| `watson/jobs/social/image_search.py` | Skeletal stub; active social posting is in `jobs/facebook/` |
| `watson/jobs/marketing/` | Not wired to any active cron or skill router entry |
| `watson/jobs/misc/` | Grab-bag of mostly undocumented stubs (here_link_book.py, im_trying_file.py, update_your_own.py) |
| `watson/jobs/acquired/` | No docstrings; purpose of chump.py and send.py unclear |
| `wcky/src/app/read/[slug]/` | Appears to duplicate `/twj/read`; same LoginForm + ManuscriptReader components; likely legacy route |

### 🟡 Known issues (from WATSON_ARCHITECTURE.md)

| File | Issue |
|------|-------|
| `watson/memory/skills.json` | References `jobs.calendar.clear_day` — should be `jobs.gcal.clear_day` |
| `watson/jobs/dashboard/app.py.bak` | Backup file committed to repo; should be gitignored or deleted |
| `watson/vercel.json` + `.vercel/` | Belong to `web/` subdirectory (old review app); pollute root-level repo config |
| `watson/CLAUDE.md` | Legacy; content superseded by `memory/WATSON_ARCHITECTURE.md` |
| `watson/memory/architecture.md` | Older architecture notes; superseded by `WATSON_ARCHITECTURE.md` |

---

## File Counts

| Repo | Tracked files (excl. noise) |
|------|-----------------------------|
| `~/watson/` | ~230 |
| `~/wcky/` | ~110 |
| `~/watson-admin/` | ~35 |
| `~/watson-ui/` | ~28 |
| **Total** | **~403** |
