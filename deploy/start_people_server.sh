#!/bin/bash
# Start the People Registry API on port 5100.
# Usage: bash deploy/start_people_server.sh
# Safe to re-run — kills any existing process on 5100 first.

set -euo pipefail

PORT=5100
WATSON_DIR="/home/billyomes/watson"
SCRIPT="$WATSON_DIR/jobs/people/server.py"
LOGFILE="$WATSON_DIR/logs/people-server.log"

# Kill any process already bound to the port
if fuser "$PORT/tcp" &>/dev/null; then
    echo "Stopping existing process on port $PORT..."
    fuser -k "$PORT/tcp"
    sleep 1
fi

mkdir -p "$(dirname "$LOGFILE")"

export PYTHONPATH="$WATSON_DIR"
cd "$WATSON_DIR"

nohup python3 "$SCRIPT" >> "$LOGFILE" 2>&1 &
echo "People Registry API started on port $PORT (PID $!)"
echo "Logs: $LOGFILE"
