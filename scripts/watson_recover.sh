#!/bin/bash
# Watson Recovery — rebuild Watson on a fresh machine from (a) the git repo
# and (b) the local restic backup on the external drive.
#
# Run manually and interactively on a fresh machine. Never scheduled or
# triggered remotely — this script uses sudo and mutates system state
# (packages, systemd units, crontab, ~/.ssh).
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
GITHUB_REPO="github.com/byomes/watson"
DEFAULT_CLONE_DIR="$HOME/watson"
RESTORE_DIR="$HOME/watson-recovery-restore"
# Absolute source prefixes as backed up by jobs/backup_local.py — restic
# restores absolute paths relative to --target, so these must match the
# paths that were actually backed up on the original machine.
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

step "Step 1/9: Installing apt packages"
if [ ! -f "deploy/apt-packages.txt" ]; then
    echo "deploy/apt-packages.txt not found in current directory — run this"
    echo "script from a cloned watson repo, or clone first (next step)."
fi
read -rp "Clone the repo now before installing packages? [Y/n] " DO_CLONE_FIRST
if [[ ! "$DO_CLONE_FIRST" =~ ^[Nn]$ ]]; then
    read -rp "Target directory for git clone [$DEFAULT_CLONE_DIR]: " CLONE_DIR
    CLONE_DIR="${CLONE_DIR:-$DEFAULT_CLONE_DIR}"
    if [ -d "$CLONE_DIR/.git" ]; then
        echo "Repo already present at $CLONE_DIR, skipping clone."
    else
        echo "Cloning $GITHUB_REPO into $CLONE_DIR..."
        git clone "https://$GITHUB_REPO.git" "$CLONE_DIR"
    fi
else
    read -rp "Path to existing watson repo checkout: " CLONE_DIR
fi

cd "$CLONE_DIR"
echo "Working in $CLONE_DIR"

echo "Installing packages from deploy/apt-packages.txt..."
sudo apt update
sudo xargs -a deploy/apt-packages.txt apt install -y

step "Step 2/9: Restoring latest snapshot from restic repo"
echo "This will prompt for the restic repo password."
mkdir -p "$RESTORE_DIR"
restic -r "$RESTIC_REPO_PATH" restore latest --target "$RESTORE_DIR"
echo "Restore complete — files are staged under $RESTORE_DIR"

step "Step 3/9: Moving restored files into place"

RESTORED_WATSON="$RESTORE_DIR$SRC_WATSON"

if [ -f "$RESTORED_WATSON/.env" ]; then
    cp "$RESTORED_WATSON/.env" "$CLONE_DIR/.env"
    echo "Restored .env"
else
    echo "WARNING: .env not found in restore — you'll need to recreate it manually"
fi

if [ -d "$RESTORED_WATSON/config" ]; then
    cp -r "$RESTORED_WATSON/config/." "$CLONE_DIR/config/"
    echo "Restored config/"
else
    echo "WARNING: config/ not found in restore"
fi

mkdir -p "$CLONE_DIR/data"
if [ -d "$RESTORED_WATSON/data/chroma" ]; then
    mkdir -p "$CLONE_DIR/data/chroma"
    cp -r "$RESTORED_WATSON/data/chroma/." "$CLONE_DIR/data/chroma/"
    echo "Restored data/chroma/"
else
    echo "WARNING: data/chroma/ not found in restore"
fi

if [ -d "$RESTORED_WATSON/memory" ]; then
    cp -r "$RESTORED_WATSON/memory/." "$CLONE_DIR/memory/"
    echo "Restored memory/"
else
    echo "WARNING: memory/ not found in restore"
fi

# The DB snapshots and crontab.txt were backed up from a randomly-named
# tempfile.TemporaryDirectory each night, so their path under the restore
# isn't fixed — find them by name instead.
DB_SNAPSHOT_DIR=$(find "$RESTORE_DIR" -type f -name "watson.db" -path "*watson-backup-*" -printf '%h\n' | head -n1 || true)
if [ -n "$DB_SNAPSHOT_DIR" ]; then
    for db in watson.db congregation.db donors.db curator.db; do
        if [ -f "$DB_SNAPSHOT_DIR/$db" ]; then
            cp "$DB_SNAPSHOT_DIR/$db" "$CLONE_DIR/data/$db"
            echo "Restored data/$db"
        else
            echo "WARNING: $db not found in restore"
        fi
    done
else
    echo "WARNING: no DB snapshot directory found in restore — databases not restored"
fi

if [ -d "$RESTORE_DIR$SRC_HOME/.ssh" ]; then
    mkdir -p "$HOME/.ssh"
    cp -r "$RESTORE_DIR$SRC_HOME/.ssh/." "$HOME/.ssh/"
    chmod 700 "$HOME/.ssh"
    find "$HOME/.ssh" -maxdepth 1 -type f ! -name "*.pub" ! -name "known_hosts" ! -name "config" -exec chmod 600 {} \;
    find "$HOME/.ssh" -maxdepth 1 -type f -name "*.pub" -exec chmod 644 {} \;
    echo "Restored ~/.ssh (private keys chmod 600)"
else
    echo "NOTICE: ~/.ssh not found in restore — see docs/RECOVERY.md true-disaster edge case"
fi

RCLONE_CONF_SRC="$RESTORE_DIR$SRC_HOME/.config/rclone/rclone.conf"
if [ -f "$RCLONE_CONF_SRC" ]; then
    mkdir -p "$HOME/.config/rclone"
    cp "$RCLONE_CONF_SRC" "$HOME/.config/rclone/rclone.conf"
    echo "Restored rclone.conf"
else
    echo "NOTICE: rclone.conf not found in restore — see docs/RECOVERY.md true-disaster edge case"
fi

CRONTAB_SNAPSHOT=$(find "$RESTORE_DIR" -type f -name "crontab.txt" -path "*watson-backup-*" | head -n1 || true)

step "Step 4/9: Creating virtualenv and installing requirements"
python3 -m venv "$CLONE_DIR/venv"
"$CLONE_DIR/venv/bin/pip" install --upgrade pip
"$CLONE_DIR/venv/bin/pip" install -r "$CLONE_DIR/requirements.txt"
echo "venv ready at $CLONE_DIR/venv"

step "Step 5/9: Restoring crontab"
if [ -n "$CRONTAB_SNAPSHOT" ] && [ -f "$CRONTAB_SNAPSHOT" ]; then
    crontab "$CRONTAB_SNAPSHOT"
    echo "Crontab restored from $CRONTAB_SNAPSHOT"
else
    echo "WARNING: no crontab snapshot found in restore — crontab not restored"
fi

step "Step 6/9: Installing systemd services"
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now watson-bot.service
sudo systemctl enable --now watson-dashboard.service
echo "watson-bot.service and watson-dashboard.service enabled and started"

step "Step 7/9: Pulling Ollama models"
if [ -f "deploy/ollama-models.txt" ]; then
    while IFS= read -r model; do
        [ -z "$model" ] && continue
        echo "Pulling $model..."
        ollama pull "$model"
    done < deploy/ollama-models.txt
else
    echo "WARNING: deploy/ollama-models.txt not found, skipping model pulls"
fi

step "Step 8/9: Starting FlareSolverr"
if [ -f "deploy/flaresolverr_run.sh" ]; then
    bash deploy/flaresolverr_run.sh
else
    echo "WARNING: deploy/flaresolverr_run.sh not found, skipping"
fi

step "Step 9/9: Automated recovery complete"
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
