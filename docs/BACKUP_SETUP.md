# Local Backup (restic → family-storage HDD) — One-Time Setup

Companion to `jobs/backup_local.py`. This is the **local/fast recovery leg**
only — it shares a power bar with the Beelink, so it is not a
disaster-recovery replacement for OneDrive (`jobs/backup.py`, unchanged,
still the offsite leg). Both legs run independently, full scope, from live
source paths.

**2026-08-22 — now also covers full recreate-from-scratch.** In addition to
the DBs/config/data below, this leg backs up the full working tree of every
actively-developed byomes repo on this box (`CODE_REPO_SOURCES` in
`jobs/backup_local.py` — watson, wcky, watson-admin, watson-ui,
watson-docs-sync, comms-desk, comms-assets, curator, bodyrec, fms),
excluding regenerable directories (`node_modules`, `venv`/`.venv`, `.next`,
`dist`, `build`, `__pycache__`). Because it's a real filesystem backup (not
just `git push`), this captures uncommitted changes and unpushed
commits/branches too — a dead Beelink no longer means losing anything that
never made it to GitHub. `scripts/watson_recover.sh` (see `docs/RECOVERY.md`)
restores this snapshot generically and re-runs each repo's install step
(`pip install -r requirements.txt`, `npm install`) automatically — it has no
hardcoded list of what's backed up, so it stays correct as this scope grows.

The drive is `/mnt/family-storage` — an already-mounted 2TB HDD that also
serves as family NAS storage. It is **not** dedicated to Watson. Watson's
data lives in an isolated, permission-locked `watson/` subfolder
(`chmod 700`, owned by `billyomes` only) specifically so the two uses don't
interfere with or expose each other, regardless of how the family share
(Samba/NFS) is configured.

None of these steps were run by Claude Code — they require `sudo` or
`restic init`, all outside the code-change scope of this build. Run them on
the Beelink (`ssh billyomes@watson`) after this PR is merged and pulled.

## 1. Create and lock down the `watson/` subfolder

```bash
sudo mkdir -p /mnt/family-storage/watson/restic-repo
sudo chown -R billyomes:billyomes /mnt/family-storage/watson
chmod 700 /mnt/family-storage/watson
```

`chmod 700` restricts the `watson/` subfolder to the `billyomes` user only —
this isolates Watson's backup data from the family NAS share no matter how
Samba/NFS exposes the parent `/mnt/family-storage` directory.

## 2. Install restic

```bash
sudo apt install restic
```

(Or grab a static binary from https://github.com/restic/restic/releases if
the apt version is too old — restic itself has no dependencies.)

## 3. Initialize the restic repo

Pick a strong password and keep it somewhere durable (it is the only way to
decrypt this backup — restic-native encryption means there's no recovery
without it). Do not reuse the OneDrive rclone credentials or any other
Watson secret.

```bash
restic -r /mnt/family-storage/watson/restic-repo init
```

It will prompt for the password interactively.

Then add that same password to `~/watson/.env`:

```
RESTIC_PASSWORD=<the password you just chose>
```

Never commit this value — `.env` is already gitignored.

## 4. Add the cron entry

```bash
crontab -e
```

Add (2:30am — ahead of the 2am doc-mirror/KB-sync chain finishing, clear of
the 3am OneDrive job):

```
30 2 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/backup_local.py
```

## 5. Manual test run

Before trusting the cron, run it by hand once and check the log:

```bash
cd ~/watson
python jobs/backup_local.py
tail -n 50 logs/backup_local.log
```

Confirm it reports `=== Local backup completed successfully ===` with no
`ERROR` lines, and that `restic -r /mnt/family-storage/watson/restic-repo
snapshots` (password prompt, or `RESTIC_PASSWORD` from `.env` exported into
your shell) shows a new snapshot.
