# Dev Loop
Status: operational as of June 27, 2026.
Location: jobs/dev_loop/loop.py, trigger.py, cleanup.py
Model: qwen2.5-coder:7b at localhost:11434
Scheduling: cron via subprocess.Popen, non-blocking
Test method: python3 -m py_compile (syntax only)
Callback: POST /api/dev-loop/callback with X-Watson-Key
Logs: ~/watson/logs/devloop-{slug}.log
Known issue: does not read existing file before iterating — fix pending
