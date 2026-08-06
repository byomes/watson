#!/bin/bash
# Start the FlareSolverr container (Cloudflare JS challenge bypass).
# Used by jobs/curator/research.py for romance.io — see
# memory/WATSON_ARCHITECTURE.md's FlareSolverr row.
# Usage: bash deploy/flaresolverr_run.sh
# Safe to re-run — skips if a container named flaresolverr already exists.

set -euo pipefail

if docker ps -a --format '{{.Names}}' | grep -qx flaresolverr; then
    echo "flaresolverr container already exists, skipping"
    exit 0
fi

docker run -d \
    --name=flaresolverr \
    --restart unless-stopped \
    -p 8191:8191 \
    -e LOG_LEVEL=info \
    ghcr.io/flaresolverr/flaresolverr:latest

echo "flaresolverr started on localhost:8191"
