"""jobs/writing/spell_checker.py — spell checking via wordfreq and pyenchant."""
import logging
import re

log = logging.getLogger(__name__)

_FREQ_THRESHOLD = 1e-6


def check_spelling(text: str) -> list:
    try:
        from wordfreq import word_frequency
        words = re.findall(r"\b[a-zA-Z]{3,}\b", text)
        flagged = []
        for word in words:
            freq = word_frequency(word.lower(), "en")
            if freq < _FREQ_THRESHOLD:
                flagged.append(word)
        return list(dict.fromkeys(flagged))
    except Exception as exc:
        log.error("check_spelling failed: %s", exc)
        return []


def suggest_corrections(word: str) -> list:
    try:
        import enchant
        d = enchant.Dict("en_US")
        if d.check(word):
            return []
        return d.suggest(word)[:5]
    except Exception as exc:
        log.debug("pyenchant suggest failed: %s", exc)
        return []


def run(message: str = None) -> str:
    if not message:
        return "Spell checker ready. Send me text to check."

    flagged = check_spelling(message)
    if not flagged:
        return "No spelling issues found."

    lines = [f"Flagged {len(flagged)} word(s):\n"]
    for word in flagged[:20]:
        suggestions = suggest_corrections(word)
        if suggestions:
            lines.append(f"• {word} → {', '.join(suggestions[:3])}")
        else:
            lines.append(f"• {word}")
    return "\n".join(lines)
