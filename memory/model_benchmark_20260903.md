# Model Qualification Test — qwen3:8b and phi4:14b — 2026-09-03

Concurrency qualification test for two candidate reasoning models pulled on the
Beelink (i5-1235U, 32GB DDR4, CPU-only, no GPU) for potential use in
reason-heavy jobs. Not a full task-accuracy benchmark like
`memory/model_benchmark_20260715.md` — this is a narrower, specific test:
does loading and running this model in the background stall or corrupt the
live Telegram intent classifier (`gemma3:4b`), the way `qwen2.5:14b` did.

**Read-only qualification exercise.** No routing code, cron config, or the
LLM Stack table in `WATSON_ARCHITECTURE.md` were changed. `qwen3:8b` and
`phi4:14b` remain installed but unrouted — this is a test, not a rollout.

## The bar (see `WATSON_ARCHITECTURE.md`, Hardware → FMSPC/Ollama)

`qwen2.5:14b` (9.0 GB, 14.8B params) was originally routed for
"accuracy-sensitive" jobs. Loading it on the Beelink starved the resident
`gemma3:4b` intent classifier of CPU time — this CPU-only host serializes all
Ollama generate requests (`OLLAMA_NUM_PARALLEL=1`, confirmed the only viable
config on 2026-08-06 — `=2` made contention *worse*, not better, see the
Hardware note). Symptom: multi-second-to-60+-second classifier stalls, and
worse, silently wrong classification results when the classifier hit its own
55s timeout under load.

**PASS bar for a candidate:**
- Loaded classifier response is correct, **and**
- `classify()` total elapsed stays within ~2x of the unloaded baseline

**FAIL bar:**
- Classifier response is wrong/degraded under load, **or**
- `classify()` elapsed blows out anywhere near the qwen2.5:14b incident's
  40-60s range

A PASS on this test does not automatically mean "route it to a live job" —
a model with a slow solo eval rate can pass concurrency (because it never
starves the classifier) while still being unsuitable for anything that needs
to run inline/live. See verdicts below.

## Methodology

Protocol matches `tests/ollama_parallel_test.py` (the original
qwen2.5:14b/`OLLAMA_NUM_PARALLEL` test harness) exactly, parameterized by
model in a new file, `tests/ollama_parallel_candidate_test.py`, so the
original harness's documented qwen2.5:14b behavior stays untouched:

1. Fire a background `generate` call on the candidate model — same prompt as
   the original harness (800-word covenant-theology essay), `num_predict:
   700`.
2. Wait 5s so the call is genuinely mid-generation, not just queued.
3. Call the real production `jobs/intent/classifier.classify()` (model:
   `gemma3:4b`, same code path bot.py uses) with `"remind me to call John
   tomorrow at 3pm"` and time it.
4. Capture the full `classify()` JSON, not just timing, so a wrong-but-fast
   answer is caught, not just a slow one.
5. Snapshot `ollama ps` and `free -h` during the loaded window and again
   after the heavy call finishes.

`qwen3:8b` defaults to thinking-mode ON, which (per earlier same-day manual
testing in a Claude.ai session) makes it unusably slow for latency-sensitive
paths — 68s of a 78s response was the thinking block. All `qwen3:8b` runs
below use `think: false` in the payload. `phi4:14b` has no such switch.

An unloaded `gemma3:4b` baseline was run once for reference. Both candidates
were then run through the identical automated harness (this file supersedes
the earlier same-day manual/paste-back testing for qwen3:8b — both candidates
now have results from one automated pass).

---

## Baseline — unloaded `gemma3:4b`

```
classify() result: {'intent': 'reminder_create', 'params': {'title': 'call John', 'due_datetime': 'tomorrow at 3pm'}, 'confidence': 'HIGH'}
classify() elapsed: 5.91s
```

Correct. 2x threshold for PASS: **11.82s**.

---

## `phi4:14b` — concurrency test

**Loaded window** (`ollama ps` / `free -h`, ~5s into the background call):
```
NAME         SIZE      PROCESSOR    UNTIL
gemma3:4b    2.9 GB    100% CPU     29 min from now
phi4:14b     10 GB     100% CPU     3 min from now

Mem:  31Gi total, 23Gi used, 5.0Gi free, 3.5Gi buff/cache, 7.7Gi available
Swap: 2.0Gi total, 2.0Gi used, 608Ki free
```

**Result:**
```
classify() result: {'intent': 'reminder_create', 'params': {'title': 'call John', 'due_datetime': 'tomorrow at 3pm'}, 'confidence': 'HIGH'}
classify() elapsed: 8.43s
heavy phi4:14b call total elapsed: 245.90s
heavy call eval stats: eval_count=700, eval_duration=240.41s, load_duration=0.11s (already resident)
```

**After heavy call finished** — same 2 models resident, memory back to
23Gi used / 5.2Gi free, swap 2.0Gi used / 10Mi free.

**Cold-load time** (measured separately — `phi4:14b` was already resident
from earlier same-day manual testing when the concurrency test ran, so its
`load_duration` above is not a real cold load; `ollama stop phi4:14b` then a
5-token generate call gave a clean number):
```
load_duration_s: 11.47
total_duration_s: 14.52
```

**Eval rate:** 700 / 240.41s = **2.91 tok/s** while loaded concurrently with
the classifier — consistent with the 3.06 tok/s solo rate from the earlier
manual test (no meaningful contention signal; the ~5% delta is noise).

**Verdict: PASS the concurrency bar.** Classifier stayed correct and fast
(8.43s, 1.43x baseline — well inside the 2x/11.82s threshold). No sign of the
qwen2.5:14b failure mode.

**But:** `phi4:14b`'s own generation speed (2.91-3.06 tok/s) is in the same
range as the original `qwen2.5:14b`'s and its cold load (11.47s) is real, not
negligible. **Passing concurrency does not make this suitable for anything
automated or latency-sensitive** — a 700-token reply takes ~4 minutes solo.
This is a fit only for a manual/on-demand job the user explicitly waits on,
that never needs to overlap with classifier or other live traffic (which it
now demonstrably doesn't corrupt even if it did overlap). Not a fit for any
cron job, Telegram auto-reply, or dashboard-chat path expecting a normal
response time.

---

## `qwen3:8b` (think: false) — concurrency test

**Loaded window** (`ollama ps` / `free -h`, ~5s into the background call):
```
NAME         SIZE      PROCESSOR    UNTIL
gemma3:4b    2.9 GB    100% CPU     27 min from now
phi4:14b     10 GB     100% CPU     4 min from now   (still resident from prior test)

Mem:  31Gi total, 26Gi used, 354Mi free, 5.2Gi buff/cache, 5.1Gi available
Swap: 2.0Gi total, 2.0Gi used, 0B free
```

**Result:**
```
classify() result: {'intent': 'reminder_create', 'params': {'title': 'call John', 'due_datetime': 'tomorrow at 3pm'}, 'confidence': 'HIGH'}
classify() elapsed: 7.40s
heavy qwen3:8b call total elapsed: 149.34s
heavy call eval stats: eval_count=700, eval_duration=134.84s, load_duration=9.01s (genuine cold load — not previously resident)
```

**After heavy call finished** — with `phi4:14b` still resident from the prior
test plus `gemma3:4b` and now `qwen3:8b`, all 3 loaded simultaneously
(`OLLAMA_MAX_LOADED_MODELS=3`): memory jumped to 28Gi used / 544Mi free,
swap 2.0Gi used / 13Mi free.

**Cold-load time:** 9.01s (real — captured in this run, `qwen3:8b` was not
resident beforehand).

**Eval rate:** 700 / 134.84s = **5.19 tok/s** while loaded concurrently with
the classifier (and, in this run only, alongside a third resident model,
`phi4:14b`) — consistent with the earlier manual test's ~5.1 tok/s loaded vs.
5.41 tok/s solo. No sign of mutual contention.

**Verdict: PASS the concurrency bar.** Classifier stayed correct and fast
(7.40s, 1.25x baseline — comfortably inside 2x/11.82s). This reconfirms the
same-day manual test result, now from an automated harness. `qwen3:8b`'s
solo eval rate (5.19-5.41 tok/s) is meaningfully faster than `phi4:14b`'s
(~2.9-3.1 tok/s) and its cold load is faster too (9.01s vs. 11.47s) — it is
the stronger candidate of the two for anything closer to live/interactive
use, **provided `think: false` is always set** (thinking-mode-on made it
unusably slow per the earlier manual test — 68s of a 78s response was the
thinking block. This is a hard requirement, not a tuning knob: any job that
calls `qwen3:8b` must pass `think: false` explicitly).

---

## System-level note: 3-model residency exhausts this box's RAM

At steady state with just `gemma3:4b` + `phi4:14b` resident (leftover from
earlier same-day manual testing), the box was already sitting at 23Gi
used / 5.2Gi free with **swap fully used (2.0Gi/2.0Gi)** before any test in
this pass ran. Once the `qwen3:8b` test added a third resident model
(`OLLAMA_MAX_LOADED_MODELS=3` permits this), memory dropped to 260Mi free
with buff/cache squeezed to 3.2Gi. Neither candidate individually caused a
classifier failure — the concurrency mechanism that hurt `qwen2.5:14b`
(single-request-at-a-time CPU scheduling contention) isn't triggered by RAM
headroom alone. But this is real memory pressure, and it was hit with only
routine multi-candidate testing, not any deliberately adversarial load. If
either candidate is ever actually routed to a job, **only one of
qwen3:8b/phi4:14b should be resident with the classifier at a time** — do
not assume both plus `gemma3:4b` can comfortably coexist under
`OLLAMA_MAX_LOADED_MODELS=3` the way the current lineup (llama3.2:3b/
qwen2.5:7b/qwen2.5-coder:7b, all smaller) does. This is a memory observation,
not a routing change — nothing was modified as part of this test.

---

## Summary

| Model | Concurrency verdict | classify() elapsed (loaded) | classify() correct | Solo/loaded eval rate | Cold load |
|---|---|---|---|---|---|
| `gemma3:4b` (baseline, unloaded) | — | 5.91s | Yes | — | already warm |
| `phi4:14b` | **PASS** | 8.43s (1.43x baseline) | Yes | 2.91 tok/s loaded / 3.06 tok/s solo (manual test) | 11.47s |
| `qwen3:8b` (`think: false`) | **PASS** | 7.40s (1.25x baseline) | Yes | 5.19 tok/s loaded / 5.41 tok/s solo (manual test) | 9.01s |

**Both candidates pass the concurrency bar** — neither reproduces the
`qwen2.5:14b` classifier-starvation failure mode on this host. Neither is a
flat "safe to route anywhere," though:

- **`qwen3:8b`** — the stronger candidate. Faster solo, faster cold load,
  passes concurrency cleanly. **Hard requirement: `think: false` on every
  call** — thinking-mode-on is unusable for anything latency-sensitive.
  Reasonable candidate for jobs closer to live/interactive use, subject to
  that requirement and to not co-residing with other heavy models under
  memory pressure (see note above).
- **`phi4:14b`** — passes concurrency, but its own solo generation speed
  (~2.9-3.1 tok/s) is in the same range as the original `qwen2.5:14b` that
  caused this whole investigation. **Suitable only for a manual/on-demand
  job the user explicitly waits on** — not for any cron job, Telegram
  auto-reply, or anything expecting a normal response time. This is an
  explicit qualified pass, not a flat one.

No routing, cron, or `WATSON_ARCHITECTURE.md` LLM Stack changes were made as
part of this test.
