# Watson Recovery

Runbook for rebuilding Watson on a fresh machine from the local restic
backup on the external drive
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

**Design note — the restore is scope-generic, not a hardcoded list.** The
script doesn't know (or need to know) that `jobs/backup_local.py` backs up
`memory/`, or `kb/documents/`, or the `curator` repo, or `~/.claude/projects`.
It mirrors *whatever the backup actually contains* under `$HOME` back to its
original absolute path. When backup scope changes in `jobs/backup_local.py`
(a new repo added to `CODE_REPO_SOURCES`, a new path added to `DIR_SOURCES`,
a new DB added to `DB_NAMES`), this script restores it correctly with no
update needed — there's no second list to keep in sync. The only
hand-maintained exceptions are things that need special handling regardless
of *what* is backed up: the DB snapshots and crontab.txt live in a
randomly-named temp directory each night (matched by `*.db` / `crontab.txt`,
not by name), and `~/.ssh` needs its permissions fixed up after restore.

In order:

1. Installs `restic` and `git` (minimal bootstrap — everything else comes
   from `deploy/apt-packages.txt` once the watson repo is restored)
2. `restic restore latest --target <staging-dir>` from the given repo path
3. Mirrors every backed-up path under `$HOME` back into place — this is
   where the watson repo itself lands (complete with `.git`, `.env`,
   `config/`, `data/`, `memory/`, `kb/documents/`, any uncommitted changes
   or unpushed commits/branches), alongside every other actively-developed
   repo (`wcky`, `curator`, `bodyrec`, etc.), `~/.ssh`, `~/.config/rclone/`,
   and `~/.claude/projects`. Falls back to a fresh `git clone` from GitHub
   only if the watson repo is unexpectedly missing from the backup (losing
   anything never pushed, in that case)
4. Overwrites `data/*.db` with the consistency-safe `sqlite3 .backup`
   snapshots from the random temp directory (the raw copies from step 3 can
   be mid-write and aren't trusted), and locates the crontab snapshot there
5. Fixes `~/.ssh` permissions (`chmod 700` dir, `600` private keys, `644`
   public keys)
6. Installs the rest of `deploy/apt-packages.txt` (python3, sqlite3, rclone,
   docker.io, ffmpeg, tailscale, etc.)
7. Creates the watson venv and `pip install -r requirements.txt`
8. Regenerates dependencies for every other restored repo — `npm install`
   where a `package.json` exists, a fresh venv + `pip install` where a
   `requirements.txt` exists (these were deliberately excluded from the
   backup itself as regenerable)
9. `crontab`s the restored crontab snapshot
10. Copies `deploy/*.service` into `/etc/systemd/system/`, runs
    `systemctl daemon-reload`, enables and starts `watson-bot.service` and
    `watson-dashboard.service`
11. Loops `ollama pull <model>` for every line in `deploy/ollama-models.txt`
12. Runs `deploy/flaresolverr_run.sh` to start the FlareSolverr container

The script prints progress at each step and ends with the manual checklist
below.

**Tested 2026-08-30** end-to-end against a synthetic fake backup in a Docker
container (steps 1-9 — restic/git bootstrap through crontab restore; steps
10-12 aren't testable in a plain container, no real init system, and are
unchanged from the previously-shipped script). Confirmed: the watson repo
restores with `.git` intact rather than falling back to a GitHub clone, the
DB overwrite step correctly picks the consistency-safe snapshot over the
raw repo-tree copy, `~/.ssh` permissions land correctly, and dependency
reinstall works for other restored repos. Found and fixed one real bug:
`deploy/apt-packages.txt` was missing `cron`, so step 9 (`crontab
$CRONTAB_SNAPSHOT`) failed with `crontab: command not found` on a minimal
fresh machine and aborted the whole script — a full Ubuntu Server ISO
usually has `cron` preinstalled, but nothing here guaranteed it.

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

### 5. Clear git's "dubious ownership" warning on the restored repo

If the account running `git` commands afterward doesn't have the exact same
uid as whichever account the backup was taken from, the first `git status`
or `git pull` in the restored `~/watson` prints a "detected dubious
ownership" warning (harmless, but easy to mistake for something broken):

```bash
git config --global --add safe.directory /home/billyomes/watson
```

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
