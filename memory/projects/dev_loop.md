# Dev Loop

**Retired 2026-09-03.** `jobs/dev_loop/` fully removed — superseded by Dev
Sandbox (`jobs/dev/sandbox_session.py`) and Watson Dev Dispatch
(`jobs/devdispatch/`), both running real Claude Code on the Beelink. Was
already ~fully dormant before removal (dashboard UI gone since 2026-07-01,
`devloop:` Telegram trigger removed 2026-09-02, `dev_projects` had 0 rows).

Original notes, kept for history:

Status: operational as of June 27, 2026.
Location: jobs/dev_loop/loop.py, trigger.py, cleanup.py
Model: qwen2.5-coder:7b at localhost:11434
Scheduling: cron via subprocess.Popen, non-blocking
Test method: python3 -m py_compile (syntax only)
Callback: POST /api/dev-loop/callback with X-Watson-Key
Logs: ~/watson/logs/devloop-{slug}.log
Known issue: does not read existing file before iterating — fix pending
