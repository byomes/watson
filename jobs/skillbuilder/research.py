"""jobs/skillbuilder/research.py — pre-build research: PyPI, GitHub, memory search."""
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

REPO = Path(__file__).resolve().parents[2]
MEMORY = REPO / "memory"
LOG_PATH = REPO / "logs" / "research.log"

_SLUG_FILLER = frozenset({
    "build", "skill", "that", "checks", "the", "a", "an", "and", "or",
    "for", "to", "with", "i", "me", "my", "job", "create", "add",
    "ability", "write", "something", "watson", "can", "you", "make",
    "new", "some", "it", "its", "is", "are", "be", "been", "has",
    "have", "will", "would", "should", "could", "get", "gets", "give",
    "gives", "send", "sends", "this", "then", "when", "how", "what",
    "which", "also", "just", "let", "use", "using",
})

log = logging.getLogger(__name__)


def _extract_keywords(description: str) -> list[str]:
    words = re.sub(r'[^a-z0-9\s]', '', description.lower()).split()
    return [w for w in words if w not in _SLUG_FILLER and len(w) > 2][:3]


def _log_research(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.utcnow().isoformat()}] {msg}\n")


def _search_pypi(keyword: str) -> str:
    try:
        resp = requests.get(f"https://pypi.org/pypi/{keyword}/json", timeout=10)
        if resp.status_code == 200:
            info = resp.json().get("info", {})
            name = info.get("name", keyword)
            version = info.get("version", "?")
            summary = (info.get("summary") or "")[:120]
            return f"PyPI: {name} {version} — {summary}"
    except Exception:
        pass
    return ""


def _search_github(keyword: str) -> str:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("WCKY_GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    results = []
    try:
        resp = requests.get(
            f"https://api.github.com/search/code?q={keyword}+language:python+user:byomes",
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            for item in resp.json().get("items", [])[:3]:
                repo = item.get("repository", {}).get("full_name", "")
                path = item.get("path", "")
                if repo and path:
                    results.append(f"  {repo}/{path}")
    except Exception:
        pass
    try:
        resp = requests.get(
            f"https://api.github.com/search/repositories?q={keyword}+language:python&sort=stars&per_page=3",
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            for item in resp.json().get("items", []):
                name = item.get("full_name", "")
                desc = (item.get("description") or "")[:80]
                if name:
                    results.append(f"  {name}: {desc}")
    except Exception:
        pass
    return ("GitHub:\n" + "\n".join(results)) if results else ""


def _search_memory(keywords: list[str]) -> str:
    coding_dir = MEMORY / "coding"
    if not coding_dir.exists():
        return ""
    matches = []
    for md_file in sorted(coding_dir.glob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        for kw in keywords:
            if kw in text.lower():
                idx = text.lower().find(kw)
                start = max(0, idx - 50)
                end = min(len(text), idx + 450)
                matches.append(f"[{md_file.name}] ...{text[start:end].strip()}...")
                break
    return "\n\n".join(matches[:3])


def research(description: str) -> str:
    """Run parallel searches and return a research context string."""
    keywords = _extract_keywords(description)
    if not keywords:
        return ""

    _log_research(f"research({description[:80]!r}) keywords={keywords}")

    pypi_results: list[str] = []
    github_results: list[str] = []
    memory_result: list[str] = []

    def do_pypi():
        for kw in keywords:
            r = _search_pypi(kw)
            if r:
                pypi_results.append(r)

    def do_github():
        for kw in keywords:
            r = _search_github(kw)
            if r:
                github_results.append(r)
                break

    def do_memory():
        r = _search_memory(keywords)
        if r:
            memory_result.append(r)

    threads = [
        threading.Thread(target=do_pypi, daemon=True),
        threading.Thread(target=do_github, daemon=True),
        threading.Thread(target=do_memory, daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    parts = [f"## Research findings for: {description}"]
    if pypi_results:
        parts.append("### PyPI\n" + "\n".join(pypi_results))
    if github_results:
        parts.append("### GitHub\n" + "\n".join(github_results))
    if memory_result:
        parts.append("### Memory\n" + memory_result[0])

    context = "\n\n".join(parts)
    _log_research(f"done — {len(context)} chars")
    return context
