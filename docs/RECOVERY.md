# Watson Recovery

Runbook for rebuilding Watson on a fresh machine from (a) the git repo and
(b) the local restic backup on the external drive
(`jobs/backup_local.py` → `/mnt/family-storage/watson/restic-repo`).

This is a **human-run, interactive** process — `scripts/watson_recover.sh`
is never scheduled or triggered remotely, and does not fully automate the
recovery (it uses `sudo` and needs judgment calls at a few points).

## Prerequisite

The external drive holding the restic repo (`/mnt/family-storage/watson/restic-repo`)
must be physically present and mounted on the new machine — either the same
drive moved over from the old Beelink, or a copy of it.

## What `scripts/watson_recover.sh` does automatically

```bash
bash scripts/watson_recover.sh /path/to/mounted/restic-repo
```

In order:

1. Installs packages from `deploy/apt-packages.txt` via `apt install -y`
   (python3, python3-venv, python3-pip, sqlite3, restic, rclone, docker.io,
   ffmpeg, git, tailscale)
2. `git clone`s `github.com/byomes/watson` (prompts for target directory,
   defaults to `~/watson`)
3. `restic restore latest --target <staging-dir>` from the given repo path
   — restores `.env`, `config/`, `data/chroma/`, `memory/`, the four DB
   snapshots, `~/.ssh`, `rclone.conf`, and the crontab snapshot
4. Moves the restored files into their real locations:
   - `.env`, `config/`, `data/chroma/`, `memory/`, and the DB snapshots into
     the cloned repo
   - `~/.ssh` keys into `~/.ssh/`, with `chmod 600` on private keys
   - `rclone.conf` into `~/.config/rclone/`
5. Creates the Python venv and `pip install -r requirements.txt`
6. `crontab`s the restored crontab snapshot
7. Copies `deploy/*.service` into `/etc/systemd/system/`, runs
   `systemctl daemon-reload`, enables and starts `watson-bot.service` and
   `watson-dashboard.service`
8. Loops `ollama pull <model>` for every line in `deploy/ollama-models.txt`
9. Runs `deploy/flaresolverr_run.sh` to start the FlareSolverr container

The script prints progress at each step and ends with the manual checklist
below.

## Manual follow-up steps (required after running the script)

### 1. Re-register with Tailscale

```bash
tailscale up
```

Follow the printed auth link. This is a fresh machine as far as Tailscale
is concerned, even with a restored config.

### 2. Verify the OneDrive rclone token

```bash
rclone lsd Watson-Backup:
```

If this lists remote directories, the restored `rclone.conf` token is still
valid — nothing further to do. If it fails (expired/revoked token), re-auth:

```bash
rclone config reconnect Watson-Backup:
```

### 3. Verify the Google Calendar OAuth token

```bash
cd ~/watson && PYTHONPATH=/home/billyomes/watson venv/bin/python jobs/gcal/token_health.py
```

If the restored `config/token.json` still refreshes, nothing further to do.
If it's dead, re-run `/gcal-auth` (dashboard route) or
`jobs/gcal/reauth.py` (terminal flow) to get a fresh token.

### 4. Rebuild Gutendex's Postgres catalog

Gutendex (`~/gutendex`, `gutendex.service`) is a **separate install from
Watson** with its own Postgres-backed book catalog. It is not covered by
`scripts/watson_recover.sh`. Follow Gutendex's own setup process to rebuild
the catalog (originally built via `finish_catalog_load.py` — see
`memory/DEV_PROJECTS.md` item #19 for known staleness caveats).

## Edge case: local backup drive also lost (true disaster, not planned upgrade)

The steps above assume the external drive (and therefore `~/.ssh` and
`rclone.conf`) survived and was restored. If the drive itself is also lost
— not just the Beelink — `scripts/watson_recover.sh` will print `NOTICE`
lines for the missing `~/.ssh` and `rclone.conf` restores, and you'll need
to handle these manually:

- **SSH keys:** generate a new keypair (`ssh-keygen`) and re-register the
  public key with GitHub (repo/deploy access) and FMSPC (for the
  FMSPC → Beelink transcript `scp` transfer, see `memory/DEV_PROJECTS.md`
  item #29).
- **rclone/OneDrive:** there is no way to recover the OneDrive leg's
  saved token — run a full fresh OAuth flow (`rclone config`) to
  re-authorize the `Watson-Backup` remote from scratch.

This only applies in the true-disaster case. If the drive was physically
moved to the new machine (planned hardware upgrade), `~/.ssh` and
`rclone.conf` restore normally along with everything else and none of this
section applies.
