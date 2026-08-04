# Trial Log: OpenCode + qwen2.5-coder:7b vs Claude Code

Retry of job_id 14 (failed with no commits between main and worktree; root cause unknown).
This run commits after every step to guarantee a trace even on failure.

- Trial started: 2026-08-04, branch `worktree-devdispatch+20260804-164515`.

## Reading / Speccing

- `project_backlog` table schema (from `~/watson/data/watson.db`, the real
  DB — this worktree's copies of `data/watson.db` / `watson.db` are empty
  placeholders since those paths are gitignored):
  ```
  CREATE TABLE project_backlog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    added_date TEXT NOT NULL DEFAULT (date('now'))
  )
  ```
  Column is `added_date` (task spec's "Added" date), format `YYYY-MM-DD`.
  Current real data: all 5 rows added 2026-07-12 (23 days old as of today,
  2026-08-04) — none currently qualify as >60 days old, so the real DB
  won't exercise the stale-row path; a synthetic fixture DB is needed for
  the test to actually cover it.
- Convention reference: `jobs/dev/bugs_backlog_sync.py` and
  `jobs/dev/file_map.py` — docstring header with cron line +
  on-demand-run note, `from core.database import get_connection`
  (`core/database.py` wraps `sqlite3.connect(config.settings.DB_PATH)`,
  `row_factory = sqlite3.Row`), no hardcoded absolute paths (`Path.home()`
  or `config.settings` instead).
- Test convention reference: `jobs/connect_cards/test_correction_handler.py`
  — plain pytest functions, no test framework classes, docstring per test
  explaining the scenario, importable via
  `PYTHONPATH=/home/billyomes/watson venv/bin/python -m pytest <path> -v`.
- Environment check: `node` v18.19.1 / `npm` 9.2.0 present. Ollama reachable
  at `localhost:11434`; `qwen2.5-coder:7b` confirmed in `ollama list` output
  alongside 10 other resident models.

## Step 1 — Install & configure OpenCode

- `npm install -g opencode-ai` failed: `EACCES: permission denied, mkdir
  '/usr/local/lib/node_modules'`. Not eligible for sudo (Watson's Claude
  Code sudo grant is restart-only, per CLAUDE.md) — used npm's own
  documented non-root mechanism instead (`npm install -g opencode-ai
  --prefix "$HOME/.npm-global"`), not a workaround around the actual
  blocker, just the correct invocation for a non-root global install.
  Succeeded: `added 5 packages in 5s`.
- Version: `opencode --version` → `1.18.13`.
- Config: found a pre-existing `~/.config/opencode/opencode.json` already
  present from the earlier failed job_id 14 attempt (timestamped 12:47,
  before this session started at ~16:45) — already correctly configured:
  a custom `ollama` provider via `@ai-sdk/openai-compatible` pointing at
  `http://localhost:11434/v1`, model `qwen2.5-coder:7b`. Matches the task
  requirement (no new model pulls, no API keys, no cloud provider) exactly,
  so left as-is rather than rewritten.
- Smoke test: `opencode run -m ollama/qwen2.5-coder:7b "Reply with exactly
  the word: PONG"`. Exit code 0, but the response was **not** a clean text
  reply — the model emitted a hallucinated tool-call-shaped JSON blob:
  `{"name":"respond_with_pong", "arguments":{"message":"PONG"}}`. There is
  no `respond_with_pong` tool in OpenCode's toolset; the model appears to
  be imitating function-calling syntax it saw in training rather than
  either replying in plain text or calling a real available tool. Wall
  clock: roughly 5-7 minutes for this single one-word-equivalent prompt
  (backgrounded after the initial 120s foreground timeout, then ran
  another several minutes) — on this CPU-only Beelink (no GPU), a 7B model
  is slow even for trivial output.
- **Result: install/config succeeded, but the first real inference call
  already surfaces a tool-calling reliability concern and a significant
  latency concern.** Proceeding to Step 2 (the real trial task) to see
  whether this generalizes.
