# Watson — Ollama Model Qualification Test
*Design spec. Run on Beelink. No FMSPC involved — CPU-only, all four local models tested in place.*

## Purpose

Watson currently assigns models by convention (`llama3.2:3b` = primary chat/intent, `qwen2.5-coder:7b` = code/structured, `phi3:mini` = background, `gemma3:1b` = fast/lightweight), but that assignment has never been empirically tested against the actual job it's doing. This test qualifies each of the four installed models on two axes — **intent classification accuracy** and **general reasoning quality** — plus a **latency/resource** check, so model-to-job assignment is based on measured results, not just convention. Output determines whether current assignments hold, should be reshuffled, or whether a new model is worth downloading and testing.

Models under test: `llama3.2:3b`, `qwen2.5-coder:7b`, `phi3:mini`, `gemma3:1b`

---

## Battery A — Intent Classification

Mirrors the job `llama3.2:3b` currently does at stage 4 of `bot.py`'s routing (after pending-action check, skill pre-checks, and skill router — this is specifically the fallback classifier).

**Test set:** 40–60 labeled Telegram-style messages, covering:
- Clear-cut cases for each of the 18 `_SKILL_PRE_CHECKS` categories (e.g. "remind me to call Dave tomorrow" → `reminder`, "kb: what did I preach on forgiveness" → `kb_search`, "cdb: who missed 3 weeks" → `congregation_query`)
- Cases that should correctly fall through to general chat (no skill match)
- Ambiguous phrasing (could plausibly match two intents)
- Typos / partial sentences / voice-transcription-style fragments (relevant given the voice pipeline conversation)
- Multi-intent messages ("remind me to call Dave and also what's my calendar look like Thursday")

**Procedure:** Run the identical labeled set through all four models via the same system prompt currently used in `intent/classifier.py`. Record predicted route vs. actual label.

**Scoring:**
- Accuracy = correct routes / total
- Confusion matrix — which intents get confused with which, per model
- Latency per classification call (matters more here than in Battery B — this runs on every message)

---

## Battery B — General Reasoning / Structured Task Quality

Mirrors downstream reasoning jobs (pastoral note extraction, report summarization, structured data pulls) — not the classifier job, the actual thinking job.

**Task types (aim for ~10–15 tasks total, mixed):**
1. **Structured extraction** — messy paragraph → JSON (e.g. extract `{name, date, topic}` from a rambling connect-card note). Scored on format compliance + correctness.
2. **Multi-step conditional instructions** — "if the member hasn't attended in 3 weeks AND isn't marked snowbird, flag them" applied to a small sample dataset. Scored on correctness.
3. **Summarization** — condense a sample shepherding-report-style input into 3 bullet points. Scored on completeness + no invented detail.
4. **Hallucination check** — ask a question where the honest answer is "not in the provided material." Pass/fail on whether the model admits it doesn't know vs. invents an answer. This one matters most given Watson's no-hallucination constraint.
5. **Basic logic/arithmetic sanity** — simple multi-step reasoning, not trick questions. Pass/fail.

**Scoring rubric per response:**
- Correctness: 0–2
- Completeness: 0–2
- Format compliance (where JSON/structure required): 0–1
- Hallucination: pass/fail (any fail is a flag regardless of other scores)

**Grading approach:** Since there's no local model stronger than what's being tested (qwen2.5:14b is gone, nothing replaces it yet), grade Battery B outputs manually or paste results back into this chat for scoring — don't use one of the four models under test to grade the others; that's circular.

---

## Battery C — Latency & Resource Cost

CPU-only Beelink, models sharing the box with `watson-bot.service`, `watson-dashboard.service`, and whatever else is running. Practical constraint, not academic.

For each model, per call:
- Wall-clock response time
- Tokens/sec
- Peak memory during inference (`top`/`htop` snapshot or cgroup stat if running under systemd)

This matters especially for the intent classifier (runs on every message — latency compounds) and for any future voice pipeline (real-time budget is tighter than Telegram).

---

## Harness

Companion script `model_qualify.py` (below) runs Battery A and C automatically against Ollama's local API and logs raw outputs for Battery B for manual/Claude-assisted grading.

**Output:** `results_YYYYMMDD.json` — per model: accuracy %, confusion matrix, avg latency, avg tokens/sec, and raw Battery B responses for grading.

---

## Decision Point (after results)

- If one model clearly wins Battery A and its latency is acceptable → confirm it as intent classifier, no change needed.
- If a model other than `llama3.2:3b` wins → reassign, cheap change, no download needed.
- If **all four** underperform on Battery B (especially the hallucination check) → that's the actual signal to consider downloading a new model, not before.
