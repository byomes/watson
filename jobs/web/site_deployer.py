"""jobs/web/site_deployer.py — Deploy Watson web properties and check Vercel status."""
import logging
import os
import re
import subprocess
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

HOME = Path.home()
VERCEL_TOKEN = os.getenv("VERCEL_TOKEN")

log = logging.getLogger(__name__)

SITES = {
    "wcky": HOME / "wcky",
    "watson-admin": HOME / "watson-admin",
    "watson-ui": HOME / "watson-ui",
}
VERCEL_PROJECTS = {
    "wcky": os.getenv("VERCEL_WCKY_PROJECT", "wcky"),
    "watson-admin": os.getenv("VERCEL_ADMIN_PROJECT", "watson-admin"),
    "watson-ui": os.getenv("VERCEL_UI_PROJECT", "watson-ui"),
}


def _git_push(repo_path: Path) -> bool:
    if not repo_path.exists():
        log.warning("Repo path does not exist: %s", repo_path)
        return False
    try:
        result = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            log.error("git push failed: %s", result.stderr.strip())
        return result.returncode == 0
    except Exception as exc:
        log.error("_git_push failed: %s", exc)
        return False


def deploy_wcky() -> bool:
    return _git_push(SITES["wcky"])


def deploy_watson_admin() -> bool:
    return _git_push(SITES["watson-admin"])


def deploy_watson_ui() -> bool:
    return _git_push(SITES["watson-ui"])


def get_deployment_status(site: str) -> str:
    if not VERCEL_TOKEN:
        return "VERCEL_TOKEN not configured."
    project = VERCEL_PROJECTS.get(site, site)
    try:
        resp = requests.get(
            f"https://api.vercel.com/v6/deployments?projectId={project}&limit=1",
            headers={"Authorization": f"Bearer {VERCEL_TOKEN}"},
            timeout=15,
        )
        resp.raise_for_status()
        deployments = resp.json().get("deployments", [])
        if not deployments:
            return f"No deployments found for {site}."
        d = deployments[0]
        return f"{site}: {d.get('state', '?')} — {d.get('url', '')} ({d.get('createdAt', '')[:10]})"
    except Exception as exc:
        log.error("get_deployment_status failed: %s", exc)
        return f"Error fetching status: {exc}"


def run(message: str = None) -> str:
    if not message:
        return "Site deployer ready. Specify a site: wcky, watson-admin, or watson-ui."
    msg = message.lower()
    if "wcky" in msg:
        ok = deploy_wcky()
        status = get_deployment_status("wcky")
        return f"wcky deploy {'succeeded' if ok else 'failed'}. {status}"
    if "admin" in msg:
        ok = deploy_watson_admin()
        return f"watson-admin deploy {'succeeded' if ok else 'failed'}."
    if "ui" in msg:
        ok = deploy_watson_ui()
        return f"watson-ui deploy {'succeeded' if ok else 'failed'}."
    return "Specify a site: wcky, watson-admin, or watson-ui."
