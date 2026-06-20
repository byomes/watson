"""
Contacts lookup skill — searches congregation.db by name.
Triggered via Telegram: "find [name]", "look up [name]", "contact [name]", etc.
"""

import re
from jobs.people.api import congregation_search, people_list

def run(message: str = None) -> str:
    if not message:
        return "Please provide a name to search."
    # Extract name from message
    pattern = r'(?:(?:find|look up|lookup|contact|who is|search for|search|pull up)\s+)+(.+)'
    match = re.search(pattern, message.strip(), re.IGNORECASE)

    if not match:
        # Try using the whole message as the name
        name = message.strip()
    else:
        name = match.group(1).strip()

    if not name:
        return "Who are you looking for?"

    results = congregation_search(name)

    if isinstance(results, dict) and 'error' in results:
        return f"Search error: {results['error']}"

    if not results:
        return f"No one found matching '{name}'."

    lines = [f"Found {len(results)} result(s) for '{name}':\n"]
    for p in results[:5]:
        line = f"• {p.get('name', '—')}"
        if p.get('email'):
            line += f"\n  {p['email']}"
        if p.get('phone'):
            line += f"\n  {p['phone']}"
        if p.get('campus'):
            line += f"\n  {p['campus']} campus"
        lines.append(line)

    if len(results) > 5:
        lines.append(f"\n...and {len(results) - 5} more.")

    return "\n".join(lines)
