"""jobs/_oneoff_delete_servantcare.py -- One-shot: fully remove the
ServantCARE Hospitality Homes search tool (wtsn.me/p/servantcare). This
is the actual source of the 1.66GB / 3181-object OneDrive footprint Bill
found under Watson-Backup/data/servantcare_images -- unlike beachhouse
(deleted in the prior one-off), this tool downloads and keeps its own
local copy of every listing photo (jobs/servantcare/scraper.py,
IMAGES_DIR). Confirmed via AskUserQuestion 2026-09-24: delete the whole
tool, not just the images.

Removes: the backend jobs/servantcare package, servantcare.db, the
1604-file local image cache, the dashboard registration in
jobs/dashboard/app.py, the frontend routes in watson-tools
(src/app/p/servantcare + src/app/api/p/servantcare), the stale doc
comment in SocialDashboard.tsx that references ServantCareSearch.tsx by
name, AND purges the already-uploaded servantcare_images backup from
OneDrive (rclone copy never deletes remote-orphaned files on its own --
without this step the 1.66GB stays on OneDrive forever even after local
deletion). No crontab entries exist for this tool (scraper only ever ran
on demand). Commits + pushes both repos (watson-tools push triggers
Vercel auto-deploy). Self-deletes after running.
"""
import shutil
import subprocess
from pathlib import Path

WATSON = Path("/home/billyomes/watson")
TOOLS = Path("/home/billyomes/watson-tools")
REMOTE_IMAGES = "Watson-Backup:Watson-Backup/data/servantcare_images"


def _rm(path: Path):
    if path.is_dir():
        shutil.rmtree(path)
        print(f"removed dir  {path}")
    elif path.exists():
        path.unlink()
        print(f"removed file {path}")


def _delete_files():
    _rm(WATSON / "jobs" / "servantcare")
    _rm(WATSON / "data" / "servantcare.db")
    _rm(WATSON / "data" / "servantcare_images")
    _rm(TOOLS / "src" / "app" / "p" / "servantcare")
    _rm(TOOLS / "src" / "app" / "api" / "p" / "servantcare")


def _purge_onedrive_backup():
    result = subprocess.run(
        ["rclone", "purge", REMOTE_IMAGES], capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"purged OneDrive backup at {REMOTE_IMAGES}")
    else:
        print(f"WARNING: rclone purge failed: {result.stderr.strip()}")


def _patch_dashboard():
    path = WATSON / "jobs" / "dashboard" / "app.py"
    text = path.read_text()
    for line in [
        "from jobs.servantcare.servantcare_web import servantcare_web_bp\n",
        "from jobs.servantcare.schema import create_tables as _servantcare_create_tables\n",
        "_servantcare_create_tables()\n",
        "app.register_blueprint(servantcare_web_bp)\n",
    ]:
        text = text.replace(line, "")
    path.write_text(text)
    print(f"patched {path}")


def _patch_social_dashboard_comment():
    path = TOOLS / "src" / "app" / "cat" / "social" / "SocialDashboard.tsx"
    text = path.read_text()
    text = text.replace(
        "into the hundreds of MB), same pattern as ServantCareSearch.tsx's\n"
        "// PHOTO_BASE.",
        "into the hundreds of MB).",
    )
    path.write_text(text)
    print(f"patched stale comment in {path}")


def _commit_watson():
    paths = [
        "jobs/servantcare",
        "data/servantcare.db",
        "data/servantcare_images",
        "jobs/dashboard/app.py",
        __file__,
    ]
    existing = [p for p in paths if (WATSON / p).exists() or p == __file__]
    subprocess.run(["git", "add"] + existing, cwd=WATSON, check=True)
    subprocess.run(
        ["git", "add", "-u", "jobs/servantcare", "data"], cwd=WATSON, check=False
    )
    msg = (
        "Remove ServantCARE Hospitality Homes search tool\n\n"
        "This was the actual source of the 1.66GB OneDrive footprint Bill "
        "flagged (data/servantcare_images) -- it downloaded and locally "
        "cached every listing photo instead of hotlinking. Confirmed via "
        "AskUserQuestion to delete the whole tool, not just the image "
        "cache; also purged the already-uploaded OneDrive copy.\n\n"
        "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_01MfcEpouVTozwhJ3eb9DZgx"
    )
    subprocess.run(["git", "commit", "-m", msg], cwd=WATSON, check=True)
    subprocess.run(["git", "push"], cwd=WATSON, check=True)
    print("watson: committed + pushed")


def _commit_tools():
    subprocess.run(
        ["git", "add", "-A", "src/app/p/servantcare", "src/app/api/p/servantcare",
         "src/app/cat/social/SocialDashboard.tsx"],
        cwd=TOOLS, check=False,
    )
    result = subprocess.run(["git", "status", "--short"], cwd=TOOLS, capture_output=True, text=True)
    if not result.stdout.strip():
        print("watson-tools: nothing to commit")
        return
    msg = (
        "Remove ServantCARE Hospitality Homes search page and API routes\n\n"
        "Backend downloaded and stored every listing photo locally, which is "
        "what was filling OneDrive backup storage. Removing at Bill's request.\n\n"
        "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_01MfcEpouVTozwhJ3eb9DZgx"
    )
    subprocess.run(["git", "commit", "-m", msg], cwd=TOOLS, check=True)
    subprocess.run(["git", "push"], cwd=TOOLS, check=True)
    print("watson-tools: committed + pushed (Vercel will auto-deploy)")


def _restart_dashboard():
    result = subprocess.run(
        ["sudo", "-n", "/usr/bin/systemctl", "restart", "watson-dashboard.service"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("restarted watson-dashboard.service")
    else:
        print(f"WARNING: dashboard restart failed: {result.stderr.strip()}")


def main():
    _delete_files()
    _purge_onedrive_backup()
    _patch_dashboard()
    _patch_social_dashboard_comment()
    _commit_watson()
    _commit_tools()
    _restart_dashboard()
    Path(__file__).unlink()
    print("self-deleted this script")


if __name__ == "__main__":
    main()
