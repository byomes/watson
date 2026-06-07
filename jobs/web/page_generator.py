"""jobs/web/page_generator.py — Generate blog posts and landing pages."""
import logging
import os
import re
from datetime import date

import requests
from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = "http://localhost:11434/api/generate"
log = logging.getLogger(__name__)


def generate_blog_page(title: str, content: str, slug: str) -> str:
    today = date.today().isoformat()
    safe_title = title.replace('"', '\\"')
    frontmatter = (
        f"---\n"
        f'title: "{safe_title}"\n'
        f"date: {today}\n"
        f"slug: {slug}\n"
        f"author: Dr. Bill Yomes\n"
        f"published: false\n"
        f"---\n\n"
    )
    return frontmatter + content


def generate_landing_page(headline: str, subhead: str, cta: str,
                          sections: list) -> str:
    section_html = ""
    for s in sections:
        section_html += (
            f'  <section class="section">\n'
            f'    <h2>{s.get("heading", "")}</h2>\n'
            f'    <p>{s.get("body", "")}</p>\n'
            f'  </section>\n'
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{headline}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'DM Sans',system-ui,sans-serif;background:#111827;color:#e8eaed;line-height:1.6}}
  .hero{{padding:80px 24px;text-align:center;border-bottom:1px solid #2a2f38}}
  h1{{font-size:clamp(2rem,5vw,3.5rem);color:#c9a84c;margin-bottom:16px}}
  .subhead{{font-size:1.25rem;color:#9ca3af;margin-bottom:32px}}
  .cta{{display:inline-block;padding:14px 32px;background:#c9a84c;color:#111827;font-weight:700;border-radius:8px;text-decoration:none}}
  .section{{padding:60px 24px;max-width:800px;margin:0 auto;border-bottom:1px solid #2a2f38}}
  h2{{font-size:1.75rem;color:#c9a84c;margin-bottom:12px}}
</style>
</head>
<body>
<div class="hero">
  <h1>{headline}</h1>
  <p class="subhead">{subhead}</p>
  <a href="#" class="cta">{cta}</a>
</div>
{section_html}</body>
</html>"""


def run(message: str = None) -> str:
    if not message:
        return "Page generator ready. Describe what page you need."
    prompt = (
        f"Extract the following from this message and return as JSON:\n"
        f'- "type": "blog" or "landing"\n'
        f'- "title": page title\n'
        f'- "slug": url slug\n'
        f'- "content": brief content or description\n\n'
        f"Message: {message}\n\nReturn only valid JSON."
    )
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": "llama3.2:3b", "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        import json
        json_match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            page_type = data.get("type", "blog")
            title = data.get("title", "Untitled")
            slug = data.get("slug", "untitled")
            content = data.get("content", message)
            if page_type == "landing":
                return generate_landing_page(title, content, "Learn More", [])
            return generate_blog_page(title, content, slug)
    except Exception as exc:
        log.error("page_generator run failed: %s", exc)
    return generate_blog_page("New Post", message, "new-post")
