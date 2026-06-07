"""jobs/writing/readability.py — Analyze text readability using textstat."""
import logging

log = logging.getLogger(__name__)


def analyze(text: str) -> dict:
    import textstat
    word_count = len(text.split())
    sentence_count = textstat.sentence_count(text)
    reading_time = round(word_count / 200, 1)
    return {
        "flesch_reading_ease": textstat.flesch_reading_ease(text),
        "flesch_kincaid_grade": textstat.flesch_kincaid_grade(text),
        "reading_time_minutes": reading_time,
        "word_count": word_count,
        "sentence_count": sentence_count,
    }


def run(message: str = None) -> str:
    if not message:
        return "Readability analyzer ready."
    try:
        r = analyze(message)
        ease = r["flesch_reading_ease"]
        if ease >= 90:
            level = "Very easy (5th grade)"
        elif ease >= 70:
            level = "Easy (6th grade)"
        elif ease >= 60:
            level = "Standard (7th-8th grade)"
        elif ease >= 50:
            level = "Fairly difficult (10th-12th grade)"
        elif ease >= 30:
            level = "Difficult (college level)"
        else:
            level = "Very difficult (professional)"
        return (
            f"Readability Report\n"
            f"──────────────────\n"
            f"Words:          {r['word_count']}\n"
            f"Sentences:      {r['sentence_count']}\n"
            f"Reading time:   {r['reading_time_minutes']} min\n"
            f"Flesch ease:    {r['flesch_reading_ease']:.1f} — {level}\n"
            f"Grade level:    {r['flesch_kincaid_grade']:.1f}"
        )
    except Exception as exc:
        log.error("readability analyze failed: %s", exc)
        return f"Analysis failed: {exc}"
