# Watson

Watson is Dr. Bill Yomes's personal AI assistant system — an orchestrated ecosystem of jobs, hardware, and interfaces. **Watson runs jobs, not agents or bots.** The old sub-agent persona naming (Charlie/Jenny/Mark) is retired.

## Required First Step

At the start of every new session, before writing or editing any code, read these two files in full:
1. `memory/WATSON_ARCHITECTURE.md` — hardware, databases, services, cron jobs, integrations, and conventions
2. `memory/FILE_MAP.md` — current file tree across `~/watson`, `~/wcky`, `~/watson-admin`, `~/watson-ui`

These are the source of truth. If anything in this file conflicts with them, they win.

## Quick orientation

- **Primary server:** Beelink EQi12 (hostname `watson`, user `billyomes`). All development happens here via Claude Code — there is no separate PC codebase; the old `D:\OneDrive\Claude\agents\watson` path is retired.
- **Primary interfaces:** Watson Dashboard (`https://watson.tail0243ff.ts.net`, port 5200, `watson-dashboard.service`) and Telegram (`@wckyWatsonbot`, `watson-bot.service`) — not Telegram alone.
- **Repo:** `github.com/byomes/watson` → `~/watson`.
- **Current major systems** (see WATSON_ARCHITECTURE.md for detail): ARC reader program, Writing Room partner community, Dev Loop (Ollama-driven code generation, triggered via Telegram `devloop:`), congregation/pastoral management, content pipeline, connect cards, Google Calendar integration.
- **Retired:** Build Pipeline (`jobs/dev/build_pipeline.py`, Claude API spec/review/approve flow) — superseded by Dev Loop; bot.py triggers removed 2026-07-03.

## Conventions

- Deploy: `cd ~/watson && git pull && sudo systemctl restart watson-bot.service watson-dashboard.service`.
- Claude Code's only sudo permission is restarting `watson-dashboard.service` and `watson-bot.service` — no other sudo command, ever.
- `PYTHONPATH=/home/billyomes/watson` must be inlined in every cron entry.
- Never commit credentials. Runtime secrets live in `~/watson/.env`; master store is `SECRETS.md` on OneDrive.
