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

## Step 2 — Trial task, attempt #1 (OpenCode + qwen2.5-coder:7b)

Prompt given to `opencode run -m ollama/qwen2.5-coder:7b "<prompt>"` (cwd =
this worktree): the exact task spec — create `jobs/dev/stale_backlog_report.py`
per `jobs/dev/file_map.py` / `jobs/dev/bugs_backlog_sync.py` conventions,
using `core.database.get_connection()`, querying `project_backlog` for
`added_date` >60 days old, plus a pytest unit test at
`jobs/dev/test_stale_backlog_report.py` against a temp/in-memory SQLite DB
(not the real watson.db), following `jobs/connect_cards/test_correction_handler.py`
conventions.

**Wall clock: 368 seconds (~6.1 min).**

**Result: FAILED — no files created.** Full raw output (12 lines total):

````
> build · qwen2.5-coder:7b

```json
{
  "name": "write",
  "arguments": {
    "content": "import datetime\nfrom core.database import get_connection\n\n\"\"\nThis script queries the project_backlog table in watson.db for rows where the added_date column is more than 60 days old and prints each matching row (title + added date) to stdout.\n\nRun: python jobs/dev/stale_backlog_report.py\n\"\"\",\n"filePath": "jobs/dev/stale_backlog_report.py"
  }
}
```
EXIT: 0
````

Two distinct failures in one output:
1. **The model never made a real tool call.** OpenCode's `build` agent has
   a genuine `write` tool, but the model printed a JSON object that merely
   *describes* a call to a tool named `"write"` as plain assistant text,
   rather than emitting the actual structured tool-call OpenCode's harness
   expects. `git status` confirms zero files were created or modified.
2. **The content it would have written is invalid Python anyway** — `""`
   as a bare statement, an unterminated/malformed docstring, and a stray
   `"filePath": ...` key bleeding out of `arguments` into what should have
   been file content. Even had the tool call gone through, this file would
   not have parsed.
3. Confirmed exit code 0 — OpenCode itself did not report an error; this
   was a silent quality failure, not a crash. This is the specific failure
   mode a shallow "did it exit 0" check would miss.

`opencode run --help` was checked for a plausible mitigating flag: OpenCode
gates tool use behind a permission system, and non-interactive runs may
silently starve on an unapproved `write` permission rather than surfacing
an error — which could explain the model degrading into describing the
call it couldn't make. Retrying once with `--auto` (auto-approve
permissions) to test this theory before concluding — logged below,
whichever way it goes.

### Retry with `--auto` (auto-approve permissions)

Same exact prompt, `opencode run --auto -m ollama/qwen2.5-coder:7b "<prompt>"`.

**Wall clock: 351 seconds (~5.9 min).**

**Result: FAILED again, same failure mode.** Raw output:

````
> build · qwen2.5-coder:7b

```json
{"name":"write","arguments":{"content":"\"\"\"\nCreate a Python script that queries the project_backlog table for rows where the added_date column is more than 60 days old, and print each matching row (title + added date) to stdout.\n\nUsage: python jobs/dev/stale_backlog_report.py\n\"\"\"\nimport sqlite3\ndate.today = lambda : '2023-10-01' # For testing purposes\n\nfrom core.database import get_connection\n\ndef main():\n    conn = get_connection()\n    cursor = conn.cursor()\n    cursor.execute(\"SELECT title, added_date FROM project_backlog WHERE added_date < date('now', '-60 days')\")\n    rows = cursor.fetchall()\n    for row in rows:\n        print(f'Title: {row[0]}, Added Date: {row[1]}')\n\nif __name__ == \"__main__\":\n    main()","filePath":"jobs/dev/stale_backlog_report.py"}}
```
EXIT: 0
````

`--auto` did not fix it — this rules out the permission-gating theory.
`git status` again confirms zero files touched. Notably the *content*
quality improved this time (valid-ish Python, correct query shape,
`get_connection()` used correctly) except for one hallucinated leftover
line — `date.today = lambda : '2023-10-01' # For testing purposes` — that
was never asked for and looks like a debugging artifact bleeding in from
training data. It also stopped after one (still-undelivered) tool call
without ever attempting the required unit test file, so even a
best-case "if this had actually written" scenario is an incomplete
deliverable, not just a syntax problem.

**Conclusion for Step 2: two independent attempts (~6 min and ~5.9 min
wall clock, ~12 min total), zero files produced either time.** This is a
structural incompatibility between OpenCode's tool-calling harness and
qwen2.5-coder:7b served through Ollama's OpenAI-compatible endpoint on
this machine — the model consistently narrates a tool call as chat text
instead of triggering OpenCode's actual function-calling path, a failure
silent enough that `opencode run` still exits 0. Not retrying further;
proceeding to Step 3 (Claude Code implementing the same spec directly).
