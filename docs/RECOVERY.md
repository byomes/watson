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
   docker.io, ffmpeg, cron, curl, ca-certificates, zstd, etc. — deliberately
   NOT tailscale or ollama, see steps 7-8)
7. Installs Tailscale via its official installer
   (`curl -fsSL https://tailscale.com/install.sh | sh`) — not from
   `deploy/apt-packages.txt`, because `tailscale` isn't in stock Ubuntu's
   apt repos and would abort step 6's atomic install of everything else
8. Installs Ollama via its official installer
   (`curl -fsSL https://ollama.com/install.sh | sh`) — same reason as
   Tailscale; also sets up and starts Ollama's own systemd service, which
   step 13 depends on
9. Creates the watson venv and `pip install -r requirements.txt`
10. Regenerates dependencies for every other restored repo — `npm install`
    where a `package.json` exists, a fresh venv + `pip install` where a
    `requirements.txt` exists (these were deliberately excluded from the
    backup itself as regenerable)
11. `crontab`s the restored crontab snapshot
12. Copies `deploy/watson-bot.service` and `deploy/watson-dashboard.service`
    into `/etc/systemd/system/` (named explicitly, not a `deploy/*.service`
    wildcard — that directory also holds `gutendex.service`, a real
    separate install this script doesn't cover, and a stale
    `people-server.service` not even enabled on the live box), runs
    `systemctl daemon-reload`, enables and starts both units
13. Loops `ollama pull <model>` for every line in `deploy/ollama-models.txt`
14. Runs `deploy/flaresolverr_run.sh` to start the FlareSolverr container

The script prints progress at each step and ends with the manual checklist
below.

**Tested 2026-08-30** end-to-end, three times, against a synthetic fake
backup:

- **Pass 1** (plain Docker container, steps 1-9 — restic/git bootstrap
  through the old step 9's crontab restore): confirmed the watson repo
  restores with `.git` intact rather than falling back to a GitHub clone,
  the DB overwrite step correctly picks the consistency-safe snapshot over
  the raw repo-tree copy, `~/.ssh` permissions land correctly, and
  dependency reinstall works for other restored repos. Found and fixed:
  `deploy/apt-packages.txt` was missing `cron`, aborting the script at the
  crontab step on any minimal/fresh machine (a full Ubuntu Server ISO
  usually has `cron` preinstalled, but nothing here guaranteed it).
- **Pass 2** (a systemd-enabled Docker container — real `systemd` as PID 1
  via `--privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw` and
  `/sbin/init`, to actually exercise `systemctl`): confirmed step 11's
  service install mechanics work for real — both `.service` files copy in,
  `daemon-reload` succeeds, `enable --now` reports "enabled" for both units
  (the underlying processes correctly fail to fully run in the fake
  environment — no real `billyomes` system user or app code — which is
  expected; the install itself is what's being verified). Steps 12-13 hit
  their existing skip branches cleanly and the script reached "Automated
  recovery complete." Found and fixed: `tailscale` isn't installable via
  plain `apt-get install` on stock Ubuntu 24.04 (`Unable to locate
  package`) — it needs Tailscale's own apt repo added first, which nothing
  in the old package list did. Since the old step 6 ran the whole
  `apt-packages.txt` list as one atomic command, this would have aborted
  *every* real recovery at that step, before venv setup, crontab, or
  systemd were ever reached. Fixed by moving `tailscale` out of
  `deploy/apt-packages.txt` and into its own step using Tailscale's
  official installer (which adds the repo itself).
- **Pass 3** (same systemd-enabled container, adding a real — but trimmed —
  Ollama pull and a real nested Docker daemon for FlareSolverr): as
  predicted from reading the code, the unmodified script died at old step
  12 with `ollama: command not found`, exit 127 — nothing in the script or
  `deploy/apt-packages.txt` ever installed the `ollama` binary itself, only
  assumed it. Once Ollama was installed manually to test downstream:
  `ollama pull llama3.2:1b` succeeded exactly matching the script's loop
  logic, and `deploy/flaresolverr_run.sh` started a real container in an
  isolated nested Docker daemon (never the host's) — `curl localhost:8191`
  returned HTTP 200, and re-running it correctly hit the "already exists,
  skipping" idempotency check with no duplicate. Fixed by adding a
  dedicated Ollama install step (step 8, its official installer). That in
  turn surfaced two more real gaps: Tailscale's installer (added in pass 2)
  needs `curl`, not in `deploy/apt-packages.txt` at the time, so it would
  have failed *before* Tailscale, venv setup, npm, crontab, systemd, or
  Ollama ever ran — and Ollama's installer additionally needs `zstd` for
  extraction. Both added to `deploy/apt-packages.txt` (along with
  `ca-certificates`, the usual companion for `curl`-based HTTPS installers).
  Two container-only artifacts surfaced during testing, not script bugs:
  the `ubuntu:24.04` base image ships a `policy-rc.d` that blocks services
  from auto-starting after `apt install` (bare-metal/VM installs don't have
  this), and nested Docker-in-Docker hit an overlay2-on-overlay2 storage
  conflict that a single non-nested daemon on real hardware won't hit.

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
