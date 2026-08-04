# Trial: OpenCode + qwen2.5-coder:7b vs. Claude Code

*Beelink EQi12, 2026-08-04. One data point — a single small, deterministic, low-ambiguity task.*

## Task

Create `jobs/dev/stale_backlog_report.py` — a deterministic (non-LLM) script that queries
`project_backlog` in `watson.db` for rows whose `added_date` is more than 60 days old, and
prints title + added date to stdout. Match `jobs/dev/` conventions (docstring header,
PYTHONPATH-safe imports, no hardcoded absolute paths). Plus a unit test in the project's
existing pytest style.

## Install experience (OpenCode)

- Package: `opencode-ai` on npm, version **1.18.13**.
- `npm install -g opencode-ai` failed — `/usr/local/lib/node_modules` isn't writable by
  `billyomes` and sudo is scoped to service restarts only on this box (see `CLAUDE.md`).
  Installed locally instead (`npm install opencode-ai` in a scratch dir, ran via
  `node_modules/.bin/opencode`) — this is a standalone trial anyway, so no global install
  was needed.
- No other friction: `npm install` completed in ~7s, 5 packages, 0 vulnerabilities.
- **Config:** OpenCode has no built-in Ollama provider preset; it needs a custom provider
  block pointed at Ollama's OpenAI-compatible endpoint. Wrote
  `~/.config/opencode/opencode.json`:
  ```json
  {
    "provider": {
      "ollama": {
        "npm": "@ai-sdk/openai-compatible",
        "options": { "baseURL": "http://localhost:11434/v1" },
        "models": { "qwen2.5-coder:7b": {} }
      }
    }
  }
  ```
  `opencode models` then listed `ollama/qwen2.5-coder:7b` — no API keys, no new model pulls.
- `opencode serve --port 4096` started cleanly and listened immediately.
- **Trivial-prompt check:** `opencode run --model ollama/qwen2.5-coder:7b "Reply with
  exactly: OK"` timed out twice (60s, then 150s) with zero output before a third attempt
  returned `OK` in 49s. For comparison, a bare `curl` straight to Ollama's `/api/generate`
  with the same model answered in 6.5s. The ~40s of extra overhead is OpenCode's `build`
  agent system prompt (full tool-definition list) being prompt-eval'd on a CPU-only 7B
  model — not a config problem, just the real cost of this stack on this hardware.

## Timing

| Attempt | Wall clock | Outcome |
|---|---|---|
| OpenCode + qwen2.5-coder:7b | **328s** (5m28s), single pass, no retries | **Failed** — no file written |
| Claude Code (this session) | **~40s** for authoring + test-pass + fixture-DB run, after the same repo/convention reading both attempts needed | **Passed** — 3/3 tests, clean run against real DB |

## OpenCode/local-model attempt — pass/fail and quality notes

**Fail.** Dispatched non-interactively via `opencode run --dir <repo> --model
ollama/qwen2.5-coder:7b --auto "<task spec>"`. After 328 seconds the entire visible output
was a single malformed JSON blob that *looks* like a `write` tool call but was emitted as
plain assistant text rather than actually invoked — the process then exited 0 with no file
created and no error surfaced:

```json
{
  "name": "write",
  "arguments": {
    "content": "#!/usr/bin/env python\n\n# jobs/dev/stale_backlog_report.py\n\ndef get_stale_backlog():
    conn = sqlite3.connect('watson.db')
    ...
```

Notes on that draft content (never actually written to disk):
- **Hallucinated schema:** queried a column named `added` — the real column is
  `added_date`. It never explored the repo (no `grep`/`read` tool calls appear in the log
  at all — this was a single wasted turn), so this was a guess from the task wording
  ("Added" date), not a hallucination recoverable by inspection.
- **Ignored the project's DB-access convention:** hardcoded `sqlite3.connect('watson.db')`
  instead of `core.database.get_connection()`, which every reference file
  (`bugs_backlog_sync.py`, `file_map.py`) uses.
- **Broken code even as a draft:** used `date`/`timedelta` without importing `datetime`,
  had a stray trailing backslash mid-statement, and the JSON itself was malformed
  (unescaped newlines/quotes) — it wouldn't have parsed as a valid tool call even if the
  harness had tried.
- **No docstring header, no `Run:` line, no test file** — none of the requested convention
  matching happened, because the run terminated after this one malformed turn.
- **No retries observed** — the agent loop did not notice the tool call failed to execute
  and try again; it just ended.

This reads as a genuine tool-calling incompatibility between OpenCode's function-calling
protocol and qwen2.5-coder:7b served through Ollama's OpenAI-compatible endpoint on this
setup, not a fixable prompt-wording issue — the model produced tool-call-shaped text
instead of an actual structured tool call, and nothing in the stack caught or retried that.

## Recommendation

**Not viable, on this data point.** Even restricted to the easiest realistic case — one
new file, one existing table, an explicit schema-adjacent reference file sitting right next
to it in the same directory, zero cross-file coordination — qwen2.5-coder:7b through
OpenCode's default `build` agent failed to produce any output at all in 328 seconds, on a
task Claude Code completed correctly (matching conventions, passing tests against both a
fixture and the real DB) in well under a minute. The failure mode (a hallucinated,
never-invoked tool call, silently exiting 0) is worse than a slow success or even a clean
error — a job dispatcher trusting this exit code would report "done" for work that never
happened. Until OpenCode's tool-calling reliably round-trips through Ollama's
OpenAI-compatible endpoint for this model, it isn't safe to offload any of Watson's
`jobs/dev/`-style local-tier work to it, even the deterministic single-file cases this
trial was designed to be favorable toward.
