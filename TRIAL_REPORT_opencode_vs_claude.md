# Trial Report: OpenCode + qwen2.5-coder:7b vs Claude Code

**Question:** can OpenCode (open-source terminal coding agent) driving the
Beelink's local Ollama model `qwen2.5-coder:7b` absorb "local tier" coding
work currently done by Claude Code, to reduce Claude API credit usage?

**Method:** one real task — implement `jobs/dev/stale_backlog_report.py`
(query `project_backlog` for rows >60 days old, print title + added date,
plus a unit test) — attempted first by OpenCode+qwen2.5-coder:7b, then by
Claude Code, from the identical spec. Full raw transcripts and timings are
in `TRIAL_LOG.md`.

## Install experience

Straightforward, no real friction once one detail was worked around:
`npm install -g opencode-ai` failed with `EACCES` against the global
`/usr/local/lib/node_modules` (this account has no general sudo — only a
narrow grant to restart two systemd services). Reinstalling to a
user-owned prefix (`npm install -g opencode-ai --prefix ~/.npm-global`)
is npm's own documented mechanism for exactly this situation, not a
workaround, and succeeded in 5 seconds. Version installed: **1.18.13**.

Configuration required zero effort: a config file already existed at
`~/.config/opencode/opencode.json` from the previous (job_id 14) attempt,
correctly wired to Ollama's OpenAI-compatible endpoint
(`http://localhost:11434/v1`) with model `qwen2.5-coder:7b` and no API
keys or cloud provider — exactly the requested setup. Left as-is.

## Timing

| Attempt | Wall clock | Outcome |
|---|---|---|
| OpenCode, smoke test ("reply PONG") | ~5–7 min | Non-text hallucinated tool-call JSON instead of a reply |
| OpenCode, real task, attempt 1 (default) | 368 sec (6.1 min) | No files written |
| OpenCode, real task, attempt 2 (`--auto`) | 351 sec (5.9 min) | No files written |
| **OpenCode total** | **~12 min across 2 attempts** | **Zero working output** |
| Claude Code, same spec | **54 sec** | Complete, tests passing |

## OpenCode / local-model quality notes

The failure was consistent and specific, not random noise: across the
smoke test and both real-task attempts, `qwen2.5-coder:7b` never emitted
an actual OpenCode tool call. Instead it printed a JSON object that
*describes* calling a tool (`{"name": "write", "arguments": {...}}`) as
plain assistant text. OpenCode's harness has no way to detect and execute
a hallucinated tool call embedded in chat output, so the run reports
`EXIT: 0` — this is a **silent failure**, not a crash; a shallow
exit-code check would have called this attempt "successful."

Two retries were attempted (default and `--auto`, to rule out a
permission-gating explanation for the first failure) — both produced the
identical failure mode, ruling out both "one-off model hiccup" and
"stuck waiting on an unapproved permission" as explanations. This points
at a structural incompatibility between OpenCode's function-calling
protocol expectations and how `qwen2.5-coder:7b` behaves when served
through Ollama's OpenAI-compatible shim on this machine, rather than
something a config tweak would likely fix.

Separately from the tool-calling failure, the *content* the model would
have written was inspected (it was present as text, just never
delivered): attempt 1's content was invalid Python (an unterminated
docstring, a stray bare `""` statement, a `"filePath"` key bleeding out
of the JSON structure into what should have been file content). Attempt
2's content was closer to valid — correct query shape, correct use of
`core.database.get_connection()` — but included one unrequested
hallucinated line (`date.today = lambda : '2023-10-01' # For testing
purposes`) and never got to the required unit test file at all before
stopping. So even setting the delivery failure aside, neither attempt's
draft content was something that could ship without further correction.

No hallucinated table/column names were observed — the model correctly
referenced `project_backlog` and `added_date` in both attempts, likely
because those names were given directly in the prompt rather than
requiring independent repo exploration.

## Recommendation

Based on this one data point, **qwen2.5-coder:7b + OpenCode is not
currently viable for this class of task** — not because of code quality
(the second attempt's draft was actually reasonably close), but because
the tool-calling integration between OpenCode and Ollama's
OpenAI-compatible endpoint for this model failed to deliver *any* file
in two independent tries, consuming ~12 minutes of wall clock to produce
nothing, against Claude Code completing the identical spec correctly in
54 seconds. If this combination is revisited, the fix to investigate
first is the tool-calling transport itself (e.g. whether Ollama's native
`/api/chat` tool-calling path behaves differently than the
OpenAI-compatible `/v1` shim OpenCode is configured against here) rather
than prompt tuning — the failure looks mechanical, not a matter of the
model needing clearer instructions.
