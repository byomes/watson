#!/bin/bash
# Watson Recovery — rebuild Watson on a fresh machine from the local restic
# backup on the external drive (jobs/backup_local.py).
#
# Run manually and interactively on a fresh machine. Never scheduled or
# triggered remotely — this script uses sudo and mutates system state
# (packages, systemd units, crontab, ~/.ssh).
#
# Design note: this script does NOT hardcode which directories get restored.
# It mirrors *everything* the backup actually contains under $HOME back to
# its original absolute path, generically. jobs/backup_local.py is the only
# place backup scope is defined — when that scope changes (a new repo added
# to CODE_REPO_SOURCES, a new path added to DIR_SOURCES), this script needs
# no update to restore it correctly. The only hand-maintained exceptions are
# things that need special handling regardless of *what* is backed up: the
# DB snapshots and crontab.txt live in a randomly-named temp directory each
# night (tempfile.TemporaryDirectory), and ~/.ssh needs its permissions
# fixed up after restore.
#
# Usage: bash scripts/watson_recover.sh /path/to/mounted/restic-repo
#
# See docs/RECOVERY.md for the full runbook, including the manual follow-up
# steps this script prints at the end.

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <path-to-restic-repo>"
    echo "  e.g. $0 /mnt/family-storage/watson/restic-repo"
    exit 1
fi

RESTIC_REPO_PATH="$1"
CLONE_DIR="$HOME/watson"
RESTORE_DIR="$HOME/watson-recovery-restore"
# Absolute source prefixes as backed up by jobs/backup_local.py — restic
# restores absolute paths relative to --target, so these must match the
# machine the backup was taken on.
SRC_WATSON="/home/billyomes/watson"
SRC_HOME="/home/billyomes"

step() {
    echo ""
    echo "=== $1 ==="
}

if [ ! -d "$RESTIC_REPO_PATH" ]; then
    echo "ERROR: restic repo path does not exist: $RESTIC_REPO_PATH"
    exit 1
fi

step "Step 1: Installing restic + git"
sudo apt-get update
sudo apt-get install -y restic git

step "Step 2: Restoring latest snapshot from restic repo"
echo "This will prompt for the restic repo password."
mkdir -p "$RESTORE_DIR"
restic -r "$RESTIC_REPO_PATH" restore latest --target "$RESTORE_DIR"
echo "Restore complete — files are staged under $RESTORE_DIR"

RESTORED_HOME="$RESTORE_DIR$SRC_HOME"

if [ ! -d "$RESTORED_HOME" ]; then
    echo "ERROR: nothing restored under $RESTORED_HOME — check the restic repo/snapshot"
    exit 1
fi

step "Step 3: Restoring everything under \$HOME generically"
echo "Mirroring every backed-up path back to its original location — whatever"
echo "jobs/backup_local.py backs up tonight is what gets restored here, with"
echo "no separate list to keep in sync."
for entry in "$RESTORED_HOME"/.??* "$RESTORED_HOME"/*; do
    [ -e "$entry" ] || continue
    name="$(basename "$entry")"
    dest="$HOME/$name"
    mkdir -p "$dest"
    cp -a "$entry/." "$dest/"
    echo "Restored ~/$name"
done

if [ ! -d "$CLONE_DIR/.git" ]; then
    echo "WARNING: $CLONE_DIR has no .git — the watson repo wasn't in this"
    echo "backup snapshot as expected. Falling back to a fresh GitHub clone"
    echo "(you will lose anything that was never pushed)."
    rm -rf "$CLONE_DIR"
    git clone "https://github.com/byomes/watson.git" "$CLONE_DIR"
fi

step "Step 4: Restoring database snapshots + crontab"
# These are backed up from a randomly-named tempfile.TemporaryDirectory each
# night (see jobs/backup_local.py's _snapshot_db/_snapshot_crontab), so their
# path under the restore isn't fixed and they land outside $SRC_HOME (under
# /tmp) — they're not covered by the generic loop above. Matched by content
# (*.db / crontab.txt) rather than a hardcoded DB name list, so a DB added
# to jobs/backup_local.py's DB_NAMES later still restores correctly here.
SNAPSHOT_DIR=$(find "$RESTORE_DIR" -type d -name "watson-backup-*" | head -n1 || true)
if [ -n "$SNAPSHOT_DIR" ]; then
    mkdir -p "$CLONE_DIR/data"
    found_db=0
    for db in "$SNAPSHOT_DIR"/*.db; do
        [ -e "$db" ] || continue
        cp "$db" "$CLONE_DIR/data/$(basename "$db")"
        echo "Restored data/$(basename "$db") (consistent snapshot, overriding the raw copy)"
        found_db=1
    done
    [ "$found_db" -eq 0 ] && echo "WARNING: no *.db files found in $SNAPSHOT_DIR"

    if [ -f "$SNAPSHOT_DIR/crontab.txt" ]; then
        CRONTAB_SNAPSHOT="$SNAPSHOT_DIR/crontab.txt"
        echo "Found crontab snapshot at $CRONTAB_SNAPSHOT"
    else
        CRONTAB_SNAPSHOT=""
        echo "WARNING: crontab.txt not found in $SNAPSHOT_DIR"
    fi
else
    CRONTAB_SNAPSHOT=""
    echo "WARNING: no watson-backup-* snapshot directory found — DBs and crontab not restored"
fi

step "Step 5: Fixing ~/.ssh permissions"
if [ -d "$HOME/.ssh" ]; then
    chmod 700 "$HOME/.ssh"
    find "$HOME/.ssh" -maxdepth 1 -type f ! -name "*.pub" ! -name "known_hosts" ! -name "config" -exec chmod 600 {} \;
    find "$HOME/.ssh" -maxdepth 1 -type f -name "*.pub" -exec chmod 644 {} \;
    echo "~/.ssh permissions fixed (private keys chmod 600)"
else
    echo "NOTICE: ~/.ssh not found in restore — see docs/RECOVERY.md true-disaster edge case"
fi

if [ ! -f "$HOME/.config/rclone/rclone.conf" ]; then
    echo "NOTICE: rclone.conf not found in restore — see docs/RECOVERY.md true-disaster edge case"
fi

cd "$CLONE_DIR"
echo "Working in $CLONE_DIR"

step "Step 6: Installing apt packages"
if [ -f "deploy/apt-packages.txt" ]; then
    sudo xargs -a deploy/apt-packages.txt apt-get install -y
else
    echo "WARNING: deploy/apt-packages.txt not found, skipping"
fi

step "Step 7: Installing Tailscale"
# Deliberately not in deploy/apt-packages.txt: it isn't in stock Ubuntu's apt
# repos, so a plain `apt-get install tailscale` fails with "Unable to locate
# package" — and since step 6 installs that whole list as one atomic xargs
# command, one missing package would abort everything after it (found by
# testing this script for real, see docs/RECOVERY.md). Tailscale's official
# installer adds its own apt repo first, then installs the package.
curl -fsSL https://tailscale.com/install.sh | sh

step "Step 8: Creating watson's virtualenv and installing requirements"
python3 -m venv "$CLONE_DIR/venv"
"$CLONE_DIR/venv/bin/pip" install --upgrade pip
"$CLONE_DIR/venv/bin/pip" install -r "$CLONE_DIR/requirements.txt"
echo "venv ready at $CLONE_DIR/venv"

step "Step 9: Reinstalling dependencies for other restored repos"
echo "Regenerating node_modules/venv for every other repo the backup"
echo "restored — these were deliberately excluded from the backup itself"
echo "(regenerable, not worth the backup size/time)."
for repo_dir in "$HOME"/*/; do
    repo_dir="${repo_dir%/}"
    [ "$repo_dir" = "$CLONE_DIR" ] && continue
    [ -d "$repo_dir/.git" ] || continue
    if [ -f "$repo_dir/package.json" ]; then
        echo "npm install in $repo_dir..."
        (cd "$repo_dir" && npm install) || echo "WARNING: npm install failed in $repo_dir — check manually"
    fi
    if [ -f "$repo_dir/requirements.txt" ]; then
        echo "Creating venv + pip install in $repo_dir..."
        (
            python3 -m venv "$repo_dir/venv" &&
            "$repo_dir/venv/bin/pip" install --upgrade pip &&
            "$repo_dir/venv/bin/pip" install -r "$repo_dir/requirements.txt"
        ) || echo "WARNING: pip install failed in $repo_dir — check manually"
    fi
done

step "Step 10: Restoring crontab"
if [ -n "$CRONTAB_SNAPSHOT" ] && [ -f "$CRONTAB_SNAPSHOT" ]; then
    crontab "$CRONTAB_SNAPSHOT"
    echo "Crontab restored from $CRONTAB_SNAPSHOT"
else
    echo "WARNING: no crontab snapshot found — crontab not restored"
fi

step "Step 11: Installing systemd services"
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now watson-bot.service
sudo systemctl enable --now watson-dashboard.service
echo "watson-bot.service and watson-dashboard.service enabled and started"

step "Step 12: Pulling Ollama models"
if [ -f "deploy/ollama-models.txt" ]; then
    while IFS= read -r model; do
        [ -z "$model" ] && continue
        echo "Pulling $model..."
        ollama pull "$model"
    done < deploy/ollama-models.txt
else
    echo "WARNING: deploy/ollama-models.txt not found, skipping model pulls"
fi

step "Step 13: Starting FlareSolverr"
if [ -f "deploy/flaresolverr_run.sh" ]; then
    bash deploy/flaresolverr_run.sh
else
    echo "WARNING: deploy/flaresolverr_run.sh not found, skipping"
fi

step "Automated recovery complete"
echo "Restore staging directory left at $RESTORE_DIR for manual inspection —"
echo "safe to delete once you've confirmed everything landed correctly."

echo ""
echo "=================================================================="
echo " MANUAL FOLLOW-UP STEPS (see docs/RECOVERY.md for full detail)"
echo "=================================================================="
echo " [ ] 1. Run 'tailscale up' to re-register this machine"
echo " [ ] 2. Verify OneDrive rclone token still works:"
echo "        rclone lsd Watson-Backup:"
echo "        Re-auth only if it fails/expired."
echo " [ ] 3. Run 'python jobs/gcal/token_health.py' to verify the restored"
echo "        Google OAuth token still refreshes. Re-run /gcal-auth only if dead."
echo " [ ] 4. Rebuild Gutendex's own Postgres catalog (separate install from"
echo "        Watson, not covered by this script)."
echo ""
echo " If ~/.ssh or rclone.conf were NOT restored above (true-disaster case,"
echo " local backup drive also lost), see the edge case in docs/RECOVERY.md:"
echo " SSH keys need manual regeneration/re-registration with GitHub/FMSPC,"
echo " and rclone needs a full fresh OAuth flow."
echo "=================================================================="
