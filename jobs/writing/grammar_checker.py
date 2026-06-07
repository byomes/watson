"""jobs/writing/grammar_checker.py — grammar checking via language-tool-python."""
import logging

log = logging.getLogger(__name__)

_tool = None


def _get_tool():
    global _tool
    if _tool is None:
        import language_tool_python
        _tool = language_tool_python.LanguageTool("en-US")
    return _tool


def check_grammar(text: str) -> list:
    try:
        tool = _get_tool()
        matches = tool.check(text)
        results = []
        for m in matches:
            results.append({
                "message": m.message,
                "context": m.context,
                "replacements": m.replacements[:3],
                "category": m.category,
            })
        return results
    except Exception as exc:
        log.error("Grammar check failed: %s", exc)
        return []


def fix_grammar(text: str) -> str:
    try:
        import language_tool_python
        tool = _get_tool()
        matches = tool.check(text)
        return language_tool_python.utils.correct(text, matches)
    except Exception as exc:
        log.error("Grammar fix failed: %s", exc)
        return text


def run(message: str = None) -> str:
    if not message:
        return "Grammar checker ready. Send me text to check."

    issues = check_grammar(message)
    if not issues:
        return "No grammar issues found."

    lines = [f"Found {len(issues)} issue(s):\n"]
    for i, issue in enumerate(issues[:10], 1):
        replacements = ", ".join(issue["replacements"]) if issue["replacements"] else "none"
        lines.append(f"{i}. [{issue['category']}] {issue['message']}")
        lines.append(f"   Context: {issue['context'][:80]}")
        lines.append(f"   Suggest: {replacements}")
    return "\n".join(lines)
