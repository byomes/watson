#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Activate virtualenv if present (standard venv or .venv)
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

python3 core/pipeline.py
