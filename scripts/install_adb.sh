#!/usr/bin/env bash
# Fetches Google's official platform-tools (adb) into ~/watson/bin/ -- no
# apt/sudo/system install. Used by jobs/congregation/fluro_client.py (and
# any future job that needs ADB access to Watson's dedicated Android
# gateway phone over Tailscale). Not committed to git (bin/ is gitignored,
# it's a ~9MB third-party binary) -- re-run this after a fresh clone or a
# disaster-recovery restore from the OneDrive leg (the local restic leg
# already captures bin/ via its full-working-tree backup, so this is only
# needed for the OneDrive-leg recovery path -- see docs/RECOVERY.md).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p bin
tmp="$(mktemp -d)"
curl -fsSL -o "$tmp/platform-tools.zip" \
  "https://dl.google.com/android/repository/platform-tools-latest-linux.zip"
unzip -q -o "$tmp/platform-tools.zip" -d "$tmp"
cp "$tmp/platform-tools/adb" bin/adb
cp -r "$tmp/platform-tools/lib64" bin/lib64 2>/dev/null || true
chmod +x bin/adb
rm -rf "$tmp"
bin/adb version
