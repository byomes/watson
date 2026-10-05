#!/bin/bash
# Candidate qualification run for models that could improve on the current
# gemma3:4b (classifier/skill router) or granite4:3b (approved lineup)
# assignment -- prompted by an email about Ollama's new "Nimble" decision
# model (blocked: needs Ollama 0.35+, still pre-release as of 2026-09-29;
# see memory note). This run instead qualifies the two candidates that are
# actually usable today:
#
#   - granite4.2:3b, granite4.2:8b  -- IBM's refresh of granite4:3b
#     (already in deploy/ollama-models.txt), Apache 2.0, 128K ctx. Not
#     pulled yet -- new to this box.
#   - qwen3:8b -- already pulled and passed BOTH concurrency and
#     mixed-traffic stress tests on 2026-09-03 (memory/model_benchmark_
#     20260903.md) but was NEVER run through Battery A (intent-accuracy).
#     Only that gap gets closed here -- Tier 2 is skipped for qwen3:8b
#     since it already has a clean, documented pass.
#
# Explicitly NOT re-tested: qwen3.5:4b (65% Battery A accuracy, 2026-09-23,
# below the gemma3:4b bar), gemma4:e2b (90% accuracy but 24s avg latency --
# unusable for a live classifier), gemma4:e4b (85% accuracy / 5.3s latency
# looked good but caused two stuck/orphaned llama-server runners under real
# classify() traffic within hours of being routed live -- disqualified
# regardless of accuracy, see jobs/intent/classifier.py comment). All three
# were already qualified and rejected in tests/model_qualify/
# candidate_results_20260923/ -- rerunning them would just burn CPU-hours
# for the same answer.
#
# gemma3:4b is included in Tier 1 as a same-run, same-conditions baseline
# so accuracy/latency deltas aren't confounded by day-to-day host load
# differences vs. the 20260715/20260903 benchmark docs.
set -uo pipefail

WATSON=/home/billyomes/watson
OUT="$WATSON/tests/model_qualify/candidate_results_20260930"
PY="$WATSON/venv/bin/python3"
TIER1_MODELS=("gemma3:4b" "granite4.2:3b" "granite4.2:8b" "qwen3:8b")
TIER2_CANDIDATES=("granite4.2:3b" "granite4.2:8b")  # qwen3:8b already has Tier 2 results (20260903)

log() { echo "[$(date '+%H:%M:%S')] $*"; }

mkdir -p "$OUT"
: > "$OUT/run.log"
exec > >(tee -a "$OUT/run.log") 2>&1

log "=== Candidate run starting (granite4.2:3b, granite4.2:8b, qwen3:8b) ==="

log "--- Pulling new candidate models ---"
ollama pull granite4.2:3b
ollama pull granite4.2:8b
ollama list

log "--- Tier 1: quality/accuracy qualification (model_qualify.py), incl. gemma3:4b baseline ---"
cd "$WATSON/tests/model_qualify"
"$PY" model_qualify.py \
    --test-set "$WATSON/tests/model_qualify/test_set.json" \
    --models "${TIER1_MODELS[@]}" \
    --out "$OUT/tier1_results.json" \
    2>&1 | tee "$OUT/tier1.log"

log "--- Tier 2a: single-candidate concurrency stress test ---"
cd "$WATSON"
"$PY" tests/ollama_parallel_candidate_test.py --baseline \
    2>&1 | tee "$OUT/stress_baseline.log"

for m in "${TIER2_CANDIDATES[@]}"; do
    log "Stress test: $m"
    safe=$(echo "$m" | tr ':.' '__')
    "$PY" tests/ollama_parallel_candidate_test.py "$m" \
        2>&1 | tee "$OUT/stress_${safe}.log"
done

log "--- Tier 2b: mixed-traffic eviction/thrash test (20 min baseline + 20 min x2 candidates) ---"
"$PY" tests/ollama_parallel_candidate_test.py --mixed-traffic --duration=1200 \
    2>&1 | tee "$OUT/mixed_baseline.log"

for m in "${TIER2_CANDIDATES[@]}"; do
    log "Mixed-traffic test: $m"
    safe=$(echo "$m" | tr ':.' '__')
    "$PY" tests/ollama_parallel_candidate_test.py --mixed-traffic --with-candidate \
        --candidate="$m" --duration=1200 \
        2>&1 | tee "$OUT/mixed_${safe}.log"
done

log "=== Candidate run complete ==="
date -Iseconds > "$OUT/DONE"
