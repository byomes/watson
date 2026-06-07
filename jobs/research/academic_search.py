"""jobs/research/academic_search.py — search arXiv and Google Scholar."""
import logging
import re

log = logging.getLogger(__name__)


def search_arxiv(query: str, max_results: int = 5) -> list:
    try:
        import arxiv
        client = arxiv.Client()
        search = arxiv.Search(query=query, max_results=max_results, sort_by=arxiv.SortCriterion.Relevance)
        results = []
        for paper in client.results(search):
            results.append({
                "title": paper.title,
                "authors": [a.name for a in paper.authors],
                "summary": paper.summary[:300].replace("\n", " "),
                "url": paper.entry_id,
                "published": paper.published.strftime("%Y-%m-%d") if paper.published else "",
            })
        return results
    except Exception as exc:
        log.error("arXiv search failed: %s", exc)
        return []


def search_scholar(query: str, max_results: int = 5) -> list:
    try:
        from scholarly import scholarly as _scholarly
        results = []
        gen = _scholarly.search_pubs(query)
        for _ in range(max_results):
            try:
                pub = next(gen)
                bib = pub.get("bib", {})
                results.append({
                    "title": bib.get("title", ""),
                    "authors": bib.get("author", []),
                    "abstract": bib.get("abstract", "")[:300],
                    "url": pub.get("pub_url", ""),
                    "year": bib.get("pub_year", ""),
                })
            except StopIteration:
                break
        return results
    except Exception as exc:
        log.error("Scholar search failed: %s", exc)
        return []


def run(message: str = None) -> str:
    if not message:
        return "Academic search ready. Ask me to search arXiv or Google Scholar."

    query = re.sub(r"(?i)(search arxiv|find academic papers|scholarly search|research papers on|search scholar)\s*:?\s*", "", message).strip()
    if not query:
        return "Please provide a search query."

    lines = [f"Academic search: {query}\n"]

    arxiv_results = search_arxiv(query)
    if arxiv_results:
        lines.append("arXiv papers:")
        for p in arxiv_results:
            authors = ", ".join(p["authors"][:2])
            lines.append(f"  • {p['title']} ({authors}, {p['published']})\n    {p['url']}")
    else:
        lines.append("arXiv: no results.")

    scholar_results = search_scholar(query)
    if scholar_results:
        lines.append("\nGoogle Scholar:")
        for p in scholar_results:
            authors = p["authors"]
            if isinstance(authors, list):
                authors = ", ".join(authors[:2])
            lines.append(f"  • {p['title']} ({authors}, {p['year']})\n    {p['url']}")
    else:
        lines.append("\nGoogle Scholar: no results.")

    return "\n".join(lines)
