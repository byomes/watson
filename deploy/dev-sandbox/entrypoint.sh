#!/bin/bash
# Dev Sandbox entrypoint — launches Claude Code inside a tmux session in the
# mounted /workspace repo clone, then serves that tmux session over ttyd so
# it's reachable as a real interactive terminal from the dashboard.
#
# -W enables client input (ttyd defaults to read-only) — this must be a
# genuinely interactive terminal since Bill is typing into it live and
# answering Claude Code's own prompts himself. No -d/--dangerously-skip-
# permissions is passed to `claude` here for the same reason: a human is
# present for every action this session takes.
set -e

tmux new-session -d -s main "claude"

exec ttyd -p 7681 -W tmux attach -t main
