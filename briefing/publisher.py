import logging
from datetime import datetime

import requests
from git import Repo, GitCommandError

from briefing.builder import build
from config.settings import BASE_DIR, DEPLOY_DIR, GITHUB_TOKEN, VERCEL_DEPLOY_HOOK

log = logging.getLogger(__name__)


def _commit_message():
    today = datetime.now()
    month = today.strftime("%B")
    return f"Watson briefing — {month} {today.day}, {today.year}"


def _stage_and_push():
    repo = Repo(BASE_DIR)

    # Stage deploy/index.html
    index_html = DEPLOY_DIR / "index.html"
    if not index_html.exists():
        raise FileNotFoundError(f"Nothing to push — {index_html} not found. Run build first.")
    repo.index.add([str(index_html.relative_to(BASE_DIR))])

    # Stage any assets
    assets_dir = DEPLOY_DIR / "assets"
    if assets_dir.exists():
        asset_files = [
            str(p.relative_to(BASE_DIR))
            for p in assets_dir.rglob("*")
            if p.is_file()
        ]
        if asset_files:
            repo.index.add(asset_files)

    # Nothing to commit if working tree is clean after staging
    if not repo.index.diff("HEAD"):
        log.info("No changes to deploy/index.html — briefing content unchanged, skipping commit")
        return False

    msg = _commit_message()
    repo.index.commit(msg)
    log.info("Committed: %s", msg)

    # Build authenticated remote URL
    push_url = repo.remotes.origin.url
    if GITHUB_TOKEN and "github.com" in push_url:
        # Inject token into HTTPS URL: https://TOKEN@github.com/...
        push_url = push_url.replace("https://", f"https://{GITHUB_TOKEN}@")

    # Build authenticated push URL (token never written to git config)
    if GITHUB_TOKEN and "github.com" in push_url and "https://" in push_url:
        push_url = push_url.replace("https://", f"https://{GITHUB_TOKEN}@")

    log.info("Pushing to main...")
    repo.git.push(push_url, "main:main")
    log.info("Push successful")
    return True


def _trigger_vercel():
    if not VERCEL_DEPLOY_HOOK:
        return
    try:
        resp = requests.post(VERCEL_DEPLOY_HOOK, timeout=10)
        resp.raise_for_status()
        log.info("Vercel deploy hook triggered (HTTP %s)", resp.status_code)
    except requests.RequestException as e:
        log.warning("Vercel deploy hook failed: %s", e)


def push():
    try:
        pushed = _stage_and_push()
    except GitCommandError as e:
        log.error("Git push failed: %s", e)
        raise
    except FileNotFoundError as e:
        log.error(str(e))
        raise

    if pushed:
        _trigger_vercel()

    return pushed


def publish_briefing():
    log.info("=== Watson publish starting ===")
    build()
    pushed = push()
    if pushed:
        log.info("=== Briefing published ===")
    else:
        log.info("=== Briefing built but not pushed (no changes) ===")
    return pushed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    publish_briefing()
