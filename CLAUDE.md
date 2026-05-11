# Watson — Jobs Architecture

Watson is a personal AI research and content system running on an HP Stream (hostname: watson) on Bill's home network. Managed via SSH and Git (github.com/byomes/watson).

**Codebase locations:**
- PC: `D:\OneDrive\Claude\agents\watson`
- Stream: `~/watson`
- Service: `watson-bot.service` (systemd)

---

## Jobs architecture

Watson runs **jobs**, not agents. The agent naming convention (Charlie, Jenny, Curator) is retired.

### Sermon pipeline jobs (run on PC — Whisper requires desktop GPU)

| Job | Entry point | Trigger |
|-----|-------------|---------|
| Watcher | `jobs/watcher.py` | Run manually or on PC startup; watches two folders |
| Transcribe | `jobs/transcribe.py` | Called by watcher |
| Cleanup | `jobs/cleanup.py` | Called by watcher after transcribe |
| Generate | `jobs/generate.py` | Called by watcher after cleanup |

**Run the watcher:**
```
python jobs/watcher.py
```

**Watch folders (configured in .env):**
- `SERMON_INCOMING_DIR` → weekly sermon → full pipeline
- `SERMON_ARCHIVE_DIR`  → old sermons  → transcription + KB only

### Pipeline stages

```
Audio dropped in incoming\
  → watcher.py detects file stability (10 sec unchanged)
  → transcribe.py (Whisper large model) → outputs/transcripts/raw/
  → cleanup.py (Claude API) → outputs/transcripts/clean/
  → generate.py (Claude API) → outputs/drafts/blog/ + outputs/drafts/social/
  → generate.py pushes draft to Vercel KV (sermon:current)
  → Telegram notification with review app link
  → Bill opens review app, edits if needed, taps Approve
  → approve-blog API → .md pushed to byomes/wcky content/blog/ → Vercel deploys
  → approve-social API → seeds written to KV social queue
```

```
Audio dropped in archive\
  → transcribe.py --mode archive → kb/
  → Telegram: "Archive transcript complete"
```

---

## Review app (web/)

Next.js app deployed to Vercel from this repo. Reads drafts from Vercel KV.
No home network exposure.

**Pages:**
- `/` — blog post review, edit, approve
- `/social` — social seeds review, edit, approve

**API routes:**
- `/api/get-draft` — reads `sermon:current` from Vercel KV
- `/api/approve-blog` — pushes `.md` to `byomes/wcky` via GitHub API
- `/api/approve-social` — writes seeds to `social:queue:{dated_slug}` in KV

---

## Output directories

```
outputs/
  transcripts/
    raw/      ← Whisper output (<stem>-raw.txt)
    clean/    ← Claude cleanup output (<stem>-clean.txt)
  drafts/
    blog/     ← staged .md files (YYYY-MM-DD-slug.md)
    social/   ← seeds JSON files (YYYY-MM-DD-slug-seeds.json)
kb/           ← archive transcripts (no pipeline, storage only)
```

---

## Environment variables

See `.env.example` for all required variables. Key additions for sermon pipeline:

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API calls in cleanup + generate |
| `WCKY_GITHUB_TOKEN` | Push approved posts to byomes/wcky |
| `WCKY_GITHUB_REPO` | Target repo (default: byomes/wcky) |
| `VERCEL_KV_REST_API_URL` | Vercel KV endpoint |
| `VERCEL_KV_REST_API_TOKEN` | Vercel KV auth |
| `SERMON_INCOMING_DIR` | Weekly audio watch folder |
| `SERMON_ARCHIVE_DIR` | Archive audio watch folder |
| `WHISPER_MODEL` | Whisper model size (default: large) |
| `REVIEW_APP_URL` | Public URL of review app |

---

## Content types

generate.py produces two content types:

- **blog** — 800–1200 word article, full markdown with frontmatter, pushed to `byomes/wcky/content/blog/`
- **social_seeds** — 5 seed hooks, stored in Vercel KV queue for the social content job

Retired from Charlie: `subsplash`, `chapter-seed`.

---

## Existing Watson systems (unchanged)

| System | Location | Purpose |
|--------|----------|---------|
| Telegram bot | `bot/bot.py` | Commands, notes, briefing delivery |
| Daily briefing | `briefing/` | Research pipeline, web app |
| Core | `core/` | DB, fetcher, scorer, summarizer |
| Library | `library/` | Knowledge base ingest + search |
| Config | `config/settings.py` | All env vars, central config |
