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

# xterm.js's own default is 13px — too small on a phone screen even before
# accounting for the viewport issue fixed by -I below. 22 was the first
# attempt (2026-08-06) and read clearly on a real phone but ran too large;
# 18 is the real-mobile-test-confirmed value as of the same day. Tune by
# changing just this one line, no other code involved.
TTYD_FONT_SIZE=18

tmux new-session -d -s main "claude"

exec ttyd -p 7681 -W \
    -t fontSize=${TTYD_FONT_SIZE} \
    -I /usr/local/share/dev-sandbox/ttyd_index.html \
    tmux attach -t main
