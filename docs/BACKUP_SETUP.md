# Local Backup (restic → external 2TB HDD) — One-Time Setup

Companion to `jobs/backup_local.py`. This is the **local/fast recovery leg**
only — it shares a power bar with the Beelink, so it is not a
disaster-recovery replacement for OneDrive (`jobs/backup.py`, unchanged,
still the offsite leg). Both legs run independently, full scope, from live
source paths.

None of these steps were run by Claude Code — they require `sudo`,
`crontab -e`, or `restic init`, all outside the code-change scope of this
build. Run them on the Beelink (`ssh billyomes@watson`) after this PR is
merged and pulled.

## 1. Find the drive's UUID

```bash
sudo blkid
```

Identify the external 2TB HDD in the output (check size/label to be sure —
don't rely on `/dev/sdX` naming, it can shift across reboots) and copy its
`UUID=...` value.

## 2. Add an `/etc/fstab` entry

```bash
sudo mkdir -p /mnt/backup-hdd
sudo nano /etc/fstab
```

Add a line (replace `<UUID>` and `<fstype>` with the real values from step 1
— `ext4` unless the drive was formatted otherwise):

```
UUID=<UUID>  /mnt/backup-hdd  <fstype>  defaults,noatime,nofail  0  2
```

`nofail` is required — without it, a missing/unplugged drive at boot will
hang the whole boot sequence.

Mount it and confirm:

```bash
sudo mount -a
mount | grep backup-hdd
```

## 3. Install restic

```bash
sudo apt install restic
```

(Or grab a static binary from https://github.com/restic/restic/releases if
the apt version is too old — restic itself has no dependencies.)

## 4. Initialize the restic repo

Pick a strong password and keep it somewhere durable (it is the only way to
decrypt this backup — restic-native encryption means there's no recovery
without it). Do not reuse the OneDrive rclone credentials or any other
Watson secret.

```bash
mkdir -p /mnt/backup-hdd/restic-repo
restic -r /mnt/backup-hdd/restic-repo init
```

It will prompt for the password interactively.

Then add that same password to `~/watson/.env`:

```
RESTIC_PASSWORD=<the password you just chose>
```

Never commit this value — `.env` is already gitignored.

## 5. Add the cron entry

```bash
crontab -e
```

Add (2:30am — ahead of the 2am doc-mirror/KB-sync chain finishing, clear of
the 3am OneDrive job):

```
30 2 * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/backup_local.py
```

## 6. Manual test run

Before trusting the cron, run it by hand once and check the log:

```bash
cd ~/watson
python jobs/backup_local.py
tail -n 50 logs/backup_local.log
```

Confirm it reports `=== Local backup completed successfully ===` with no
`ERROR` lines, and that `restic -r /mnt/backup-hdd/restic-repo snapshots`
(password prompt, or `RESTIC_PASSWORD` from `.env` exported into your shell)
shows a new snapshot.
