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
