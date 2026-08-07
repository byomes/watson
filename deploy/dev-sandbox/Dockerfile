# Dev Sandbox image — interactive, human-attended Claude Code sessions.
#
# Deliberately minimal: git, node/npm (for the Claude Code CLI), tmux, ttyd.
# No app-server, no model-routing layer, no agent-server — Claude Code
# handles its own auth and model access directly, the same way it does when
# run at a terminal on the host. This is NOT the OpenHands pattern from
# earlier tonight (see jobs/dev/sandbox_session.py for the full auth
# writeup) — there is no orchestration layer here to route around.
#
# Runs as a fixed non-root user whose UID/GID are set to match the Beelink
# host user (billyomes, 1000:1000) at container-run time via `--user`, so
# a read-write bind mount of the host's real ~/.claude (mode 700-ish,
# owner-only) just works — no permission workaround, no credential copy.

FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        tmux \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Static ttyd binary (Debian's apt ttyd build has had version drift across
# releases; the upstream static binary is simpler to pin and verify).
ARG TTYD_VERSION=1.7.4
RUN curl -fsSL -o /usr/local/bin/ttyd \
        "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.x86_64" \
    && chmod +x /usr/local/bin/ttyd

RUN npm install -g @anthropic-ai/claude-code

# The node:22 base image already ships a UID/GID 1000:1000 user ("node",
# home /home/node) — reused as-is rather than creating a second one. The
# real bind-mount permission match comes from also passing
# `docker run --user 1000:1000`, not from anything UID-specific baked into
# the image (host UID isn't knowable at build time in general — it just
# happens to already be 1000 on this box, same as the "node" user here).

COPY entrypoint.sh /usr/local/bin/dev-sandbox-entrypoint.sh
RUN chmod +x /usr/local/bin/dev-sandbox-entrypoint.sh

USER node
WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/dev-sandbox-entrypoint.sh"]
