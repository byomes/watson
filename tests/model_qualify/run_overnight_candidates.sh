#!/bin/bash
# Overnight qualification run for candidate models gemma4:e2b, gemma4:e4b,
# qwen3.5:4b -- pulled 2026-09-22 for consideration as successors to
# gemma3:4b (classifier) and qwen2.5:7b (workhorse). Run detached (setsid
# nohup), scheduled at Bill's request while he sleeps. Results consumed by
# jobs/dev/_oneoff_model_candidate_report_9am.py at 9am.
set -uo pipefail

WATSON=/home/billyomes/watson
OUT="$WATSON/tests/model_qualify/candidate_results_20260923"
PY="$WATSON/venv/bin/python3"
CANDIDATES=("gemma4:e2b" "gemma4:e4b" "qwen3.5:4b")

log() { echo "[$(date '+%H:%M:%S')] $*"; }

mkdir -p "$OUT"
: > "$OUT/run.log"
exec > >(tee -a "$OUT/run.log") 2>&1

log "=== Overnight candidate run starting ==="

log "--- Pulling remaining candidate model ---"
ollama pull qwen3.5:4b
ollama list

log "--- Tier 1: quality/accuracy qualification (model_qualify.py) ---"
cd "$WATSON/tests/model_qualify"
"$PY" model_qualify.py \
    --test-set "$WATSON/tests/model_qualify/test_set.json" \
    --models "${CANDIDATES[@]}" \
    --out "$OUT/tier1_results.json" \
    2>&1 | tee "$OUT/tier1.log"

log "--- Tier 2a: single-candidate concurrency stress test ---"
cd "$WATSON"
"$PY" tests/ollama_parallel_candidate_test.py --baseline \
    2>&1 | tee "$OUT/stress_baseline.log"

for m in "${CANDIDATES[@]}"; do
    log "Stress test: $m"
    safe=$(echo "$m" | tr ':.' '__')
    "$PY" tests/ollama_parallel_candidate_test.py "$m" --think=false \
        2>&1 | tee "$OUT/stress_${safe}.log"
done

log "--- Tier 2b: mixed-traffic eviction/thrash test (20 min baseline + 20 min x3 candidates) ---"
"$PY" tests/ollama_parallel_candidate_test.py --mixed-traffic --duration=1200 \
    2>&1 | tee "$OUT/mixed_baseline.log"

for m in "${CANDIDATES[@]}"; do
    log "Mixed-traffic test: $m"
    safe=$(echo "$m" | tr ':.' '__')
    "$PY" tests/ollama_parallel_candidate_test.py --mixed-traffic --with-candidate \
        --candidate="$m" --duration=1200 \
        2>&1 | tee "$OUT/mixed_${safe}.log"
done

log "=== Overnight candidate run complete ==="
date -Iseconds > "$OUT/DONE"
