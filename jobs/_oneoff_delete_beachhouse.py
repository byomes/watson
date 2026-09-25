"""jobs/_oneoff_delete_beachhouse.py -- One-shot: fully remove the
Getaway Search / Beach House tool (wtsn.me/p/beachhouse). Bill said the
tool isn't working for what they want and to delete it and all
associated files (2026-09-24). Removes: both crontab entries (weekly
scraper + daily flash deals), the backend jobs/beachhouse package, the
beachhouse.db + backup, its logs, the dashboard registration in
jobs/dashboard/app.py, the "beachhouse" entry in devdispatch's project
list, the frontend routes in watson-tools (src/app/p/beachhouse +
src/app/api/p/beachhouse), and a stray leftover
"watson/~/watson-tools/..." directory from an old bad-path bug. Commits
+ pushes both repos (watson-tools push triggers Vercel auto-deploy via
its GitHub integration). Self-deletes after running -- run once, then
gone.
"""
import re
import shutil
import subprocess
from pathlib import Path

WATSON = Path("/home/billyomes/watson")
TOOLS = Path("/home/billyomes/watson-tools")

CRON_MARKERS = ["jobs.beachhouse.scraper", "jobs.beachhouse.flash_scraper"]


def _clean_crontab():
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    lines = current.splitlines()
    keep = []
    skip_next_is_job = False
    for line in lines:
        if any(m in line for m in CRON_MARKERS):
            continue
        if skip_next_is_job:
            skip_next_is_job = False
        if line.strip().startswith("#") and (
            "Beach House Search" in line or "Flash Deals" in line
        ):
            continue
        keep.append(line)
    new_crontab = "\n".join(keep).rstrip("\n") + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
    print("crontab: removed beachhouse scraper + flash deals entries")


def _rm(path: Path):
    if path.is_dir():
        shutil.rmtree(path)
        print(f"removed dir  {path}")
    elif path.exists():
        path.unlink()
        print(f"removed file {path}")


def _delete_files():
    _rm(WATSON / "jobs" / "beachhouse")
    _rm(WATSON / "data" / "beachhouse.db")
    _rm(WATSON / "data" / "beachhouse.db.bak-pre-categories")
    _rm(WATSON / "logs" / "beachhouse_scraper.log")
    _rm(WATSON / "logs" / "beachhouse_scraper.log.1")
    _rm(WATSON / "logs" / "beachhouse_flash_scraper.log")
    _rm(WATSON / "logs" / "beachhouse_flash_scraper.log.1")
    _rm(TOOLS / "src" / "app" / "p" / "beachhouse")
    _rm(TOOLS / "src" / "app" / "api" / "p" / "beachhouse")
    # stray leftover dir from an old bad-path bug -- only the beachhouse
    # branch of it; a sibling catalystdb stray dir lives alongside it and
    # is out of scope for this task, leave it alone
    _rm(WATSON / "~" / "watson-tools" / "src" / "app" / "api" / "p" / "beachhouse")


def _patch_dashboard():
    path = WATSON / "jobs" / "dashboard" / "app.py"
    text = path.read_text()
    for line in [
        "from jobs.beachhouse.beachhouse_web import beachhouse_web_bp\n",
        "from jobs.beachhouse.schema import create_tables as _beachhouse_create_tables\n",
        "_beachhouse_create_tables()\n",
        "app.register_blueprint(beachhouse_web_bp)\n",
    ]:
        text = text.replace(line, "")
    path.write_text(text)
    print(f"patched {path}")


def _patch_devdispatch():
    path = WATSON / "jobs" / "devdispatch" / "scheduled.py"
    text = path.read_text()
    text = re.sub(r'\s*"beachhouse",', "", text, count=1)
    path.write_text(text)
    print(f"patched {path}")


def _commit_watson():
    paths = [
        "jobs/beachhouse",
        "data/beachhouse.db",
        "data/beachhouse.db.bak-pre-categories",
        "logs/beachhouse_scraper.log",
        "logs/beachhouse_scraper.log.1",
        "logs/beachhouse_flash_scraper.log",
        "logs/beachhouse_flash_scraper.log.1",
        "jobs/dashboard/app.py",
        "jobs/devdispatch/scheduled.py",
        __file__,
    ]
    existing = [p for p in paths if (WATSON / p).exists() or p == __file__]
    subprocess.run(["git", "add"] + existing, cwd=WATSON, check=True)
    subprocess.run(
        ["git", "add", "-u", "jobs/beachhouse", "data", "logs"],
        cwd=WATSON,
        check=False,
    )
    msg = (
        "Remove Getaway Search / beachhouse tool\n\n"
        "Bill said the tool isn't working for what they want and asked to "
        "delete it entirely, including its OneDrive-backed data footprint.\n\n"
        "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_01MfcEpouVTozwhJ3eb9DZgx"
    )
    subprocess.run(["git", "commit", "-m", msg], cwd=WATSON, check=True)
    subprocess.run(["git", "push"], cwd=WATSON, check=True)
    print("watson: committed + pushed")


def _commit_tools():
    subprocess.run(["git", "add", "-A", "src/app/p/beachhouse", "src/app/api/p/beachhouse"],
                    cwd=TOOLS, check=False)
    result = subprocess.run(["git", "status", "--short"], cwd=TOOLS, capture_output=True, text=True)
    if not result.stdout.strip():
        print("watson-tools: nothing to commit")
        return
    msg = (
        "Remove Getaway Search (beachhouse) page and API routes\n\n"
        "Tool wasn't meeting the need; removing it and its backend at Bill's request.\n\n"
        "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_01MfcEpouVTozwhJ3eb9DZgx"
    )
    subprocess.run(["git", "commit", "-m", msg], cwd=TOOLS, check=True)
    subprocess.run(["git", "push"], cwd=TOOLS, check=True)
    print("watson-tools: committed + pushed (Vercel will auto-deploy)")


def _restart_dashboard():
    subprocess.run(["sudo", "-n", "systemctl", "restart", "watson-dashboard"], check=False)
    print("restarted watson-dashboard")


def main():
    _clean_crontab()
    _delete_files()
    _patch_dashboard()
    _patch_devdispatch()
    _commit_watson()
    _commit_tools()
    _restart_dashboard()
    Path(__file__).unlink()
    print("self-deleted this script")


if __name__ == "__main__":
    main()
