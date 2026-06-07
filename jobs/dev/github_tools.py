"""jobs/dev/github_tools.py — Interact with the Watson GitHub repo via PyGithub."""
import logging
import os
from dotenv import load_dotenv

load_dotenv()

REPO_NAME = "byomes/watson"
log = logging.getLogger(__name__)


def _get_repo():
    from github import Github
    token = os.getenv("GITHUB_TOKEN") or os.getenv("WCKY_GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set")
    return Github(token).get_repo(REPO_NAME)


def get_repo_info() -> dict:
    try:
        repo = _get_repo()
        return {
            "name": repo.full_name,
            "stars": repo.stargazers_count,
            "forks": repo.forks_count,
            "open_issues": repo.open_issues_count,
            "last_push": repo.pushed_at.isoformat() if repo.pushed_at else "",
            "default_branch": repo.default_branch,
        }
    except Exception as exc:
        log.error("get_repo_info failed: %s", exc)
        return {}


def create_issue(title: str, body: str) -> str:
    try:
        repo = _get_repo()
        issue = repo.create_issue(title=title, body=body)
        return issue.html_url
    except Exception as exc:
        log.error("create_issue failed: %s", exc)
        return f"Error: {exc}"


def get_open_issues() -> list:
    try:
        repo = _get_repo()
        return [
            {"number": i.number, "title": i.title, "url": i.html_url}
            for i in repo.get_issues(state="open")
        ][:20]
    except Exception as exc:
        log.error("get_open_issues failed: %s", exc)
        return []


def search_code(query: str) -> list:
    try:
        from github import Github
        token = os.getenv("GITHUB_TOKEN") or os.getenv("WCKY_GITHUB_TOKEN")
        g = Github(token)
        results = g.search_code(f"{query} repo:{REPO_NAME}")
        return [
            {"path": r.path, "url": r.html_url}
            for r in results
        ][:10]
    except Exception as exc:
        log.error("search_code failed: %s", exc)
        return []


def run(message: str = None) -> str:
    try:
        info = get_repo_info()
        if not info:
            return "GitHub tools ready. (No token configured.)"
        return (
            f"GitHub: {info['name']}\n"
            f"Stars: {info['stars']}  Forks: {info['forks']}\n"
            f"Open issues: {info['open_issues']}\n"
            f"Last push: {info['last_push'][:10]}"
        )
    except Exception as exc:
        return f"GitHub tools ready. Error: {exc}"
