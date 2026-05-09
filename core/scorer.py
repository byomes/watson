"""
Filter and score candidate items for the daily briefing.

Two-stage pipeline:
  1. filter_pool()      — freshness + content quality hard rejects
  2. score_and_select() — authority scoring, keyword boosts, guaranteed slots
"""
import logging
import re
from datetime import datetime, timezone

from config.settings import FRESHNESS_DAYS

log = logging.getLogger(__name__)

BRIEFING_SIZE = 20

# ── Scoring tables ─────────────────────────────────────────────────────────

_PRIORITY_BASE = {1: 30, 2: 20, 3: 10}

_TYPE_BONUS = {
    "article":     5,
    "journal":     8,
    "podcast":     0,
    "publication": 5,
}

# Source-level authority bonus — mapped to exact source_name values from sources.yaml.
# PhD scholars with active research profiles
_SCHOLARS = {
    "Gary Habermas", "Mike Licona",
    "William Lane Craig", "William Lane Craig (Podcast)",
    "J.P. Moreland", "Greg Koukl", "John Lennox",
    "Rebecca McLaughlin", "Nancy Pearcey",
}
# Respected ministry authors / pastor-theologians
_MINISTRY = {
    "Tim Keller", "N.T. Wright", "John MacArthur",
    "Justin Brierley", "Sean McDowell", "Sean McDowell (Podcast)",
    "Frank Turek", "Frank Turek (Podcast)", "Cross Examined (Turek)",
}
# Organization publications
_ORGS = {
    "The Gospel Coalition",
    "Stand to Reason", "Stand to Reason (Podcast)",
    "Reasons to Believe",
    "Answers in Genesis", "Answers in Genesis (Podcast)",
}

def _authority_bonus(source_name: str, source_type: str) -> int:
    if source_name in _SCHOLARS:
        return 15
    if source_name in _MINISTRY:
        return 10
    if source_name in _ORGS:
        return 5
    if source_type == "journal":
        return 20  # academic journal regardless of source name
    return 0

# ── Keyword signals ────────────────────────────────────────────────────────

_BOOST = re.compile(
    r"\b(atonement|resurrection|hermeneutics|exegesis|apologetics|theology|"
    r"scripture|canon|christology|epistemology|worldview|argument|evidence|"
    r"historical|philosophy|doctrine|biblical|gospel|trinity|incarnation)\b",
    re.IGNORECASE,
)
_PENALTY = re.compile(
    r"\b(parenting|kids|children|podcast|culture|influencer|gen\s*z|women|"
    r"movie|film|music|playlist|recipe)\b",
    re.IGNORECASE,
)

# ── Quality filter patterns ────────────────────────────────────────────────

_LISTICLE   = re.compile(r"^\d+\s")
_CLICKBAIT  = re.compile(
    r"\b(you need to|here'?s why|this is why|what you need|"
    r"going viral|breaking|shocking)\b",
    re.IGNORECASE,
)

# ── Guaranteed slots (Tier 1 apologetics authors) ──────────────────────────

_TIER1_APOLOGISTS = {
    "Gary Habermas",
    "Mike Licona",
    "William Lane Craig",
    "William Lane Craig (Podcast)",
    "Greg Koukl",
    "Justin Brierley",
    "Sean McDowell",
}


# ── Stage 1: Filter ────────────────────────────────────────────────────────

def _age_days(published_at: str) -> float | None:
    """Return age in days from published_at ISO string, or None if unparseable."""
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except (ValueError, TypeError, AttributeError):
        return None


def _quality_reject(item: dict) -> str | None:
    """Return a rejection reason string, or None if the item passes quality."""
    title = item.get("title", "")
    if _LISTICLE.match(title):
        return "listicle"
    if _CLICKBAIT.search(title):
        return "clickbait"
    # Short-summary check only when we actually had article content to extract from
    if item.get("has_content"):
        words = len((item.get("summary") or "").split())
        if words < 30:
            return "thin_summary"
    return None


def filter_pool(pool: list[dict]) -> tuple[list[dict], dict]:
    """
    Apply freshness and content-quality filters.

    Returns:
        (passed_items, stats_dict) where stats keys are:
          rejected_age, rejected_listicle, rejected_clickbait,
          rejected_thin, date_unknown_kept
    """
    passed = []
    stats  = {
        "rejected_age":       0,
        "rejected_listicle":  0,
        "rejected_clickbait": 0,
        "rejected_thin":      0,
        "date_unknown_kept":  0,
    }

    for item in pool:
        date_unknown = item.get("date_unknown", False)
        published_at = item.get("published_at")

        # Freshness filter — skip only when date is KNOWN and stale
        if not date_unknown and published_at:
            age = _age_days(published_at)
            if age is not None and age > FRESHNESS_DAYS:
                stats["rejected_age"] += 1
                log.debug("Age reject (%.1fd): %s", age, item.get("title", "")[:60])
                continue

        # Content quality filter
        reason = _quality_reject(item)
        if reason:
            stats[f"rejected_{reason}"] += 1
            log.debug("Quality reject (%s): %s", reason, item.get("title", "")[:60])
            continue

        if date_unknown:
            stats["date_unknown_kept"] += 1

        passed.append(item)

    rejected_quality = (
        stats["rejected_listicle"] +
        stats["rejected_clickbait"] +
        stats["rejected_thin"]
    )
    log.info(
        "Filter: %d/%d passed  "
        "(age=%d | quality=%d [listicle=%d clickbait=%d thin=%d] | unknown_date=%d kept)",
        len(passed), len(pool),
        stats["rejected_age"], rejected_quality,
        stats["rejected_listicle"], stats["rejected_clickbait"], stats["rejected_thin"],
        stats["date_unknown_kept"],
    )
    return passed, stats


# ── Stage 2: Score and select ──────────────────────────────────────────────

def _score_item(item: dict) -> int:
    priority    = item.get("priority", 3)
    source_type = item.get("source_type", "article")
    source_name = item.get("source_name", "")
    text        = f"{item.get('title', '')} {item.get('summary', '')}"

    score  = _PRIORITY_BASE.get(priority, 10)
    score += _TYPE_BONUS.get(source_type, 0)
    score += _authority_bonus(source_name, source_type)
    if item.get("date_unknown"):
        score -= 5
    if _BOOST.search(text):
        score += 15
    if _PENALTY.search(text):
        score -= 10
    return score


def score_and_select(pool: list[dict], n: int = BRIEFING_SIZE) -> tuple[list[dict], dict]:
    """
    Score all candidates. Guarantee one slot per active Tier 1 apologist,
    then fill remaining slots with highest scorers.

    Returns:
        (selected_items, stats_dict) where stats keys are:
          top_score, bottom_score, guaranteed_slots, guaranteed_names
    """
    if not pool:
        return [], {"top_score": 0, "bottom_score": 0, "guaranteed_slots": 0, "guaranteed_names": []}

    for item in pool:
        item["score"] = _score_item(item)

    by_score      = sorted(pool, key=lambda x: x["score"], reverse=True)
    selected      = []
    selected_urls = set()
    guaranteed    = []

    # Guaranteed slots — best-scoring item per Tier 1 apologist source
    for name in _TIER1_APOLOGISTS:
        for item in by_score:
            if item["source_name"] == name and item["url"] not in selected_urls:
                selected.append(item)
                selected_urls.add(item["url"])
                guaranteed.append(name)
                log.debug("Guaranteed: %s (score=%d)", name, item["score"])
                break

    # Fill remaining slots from top scorers
    for item in by_score:
        if len(selected) >= n:
            break
        if item["url"] not in selected_urls:
            selected.append(item)
            selected_urls.add(item["url"])

    selected.sort(key=lambda x: x["score"], reverse=True)
    result = selected[:n]

    stats = {
        "top_score":         result[0]["score"]  if result else 0,
        "bottom_score":      result[-1]["score"] if result else 0,
        "guaranteed_slots":  len(guaranteed),
        "guaranteed_names":  guaranteed,
    }

    log.info(
        "Scored %d candidates → selected %d  (scores %d–%d | guaranteed: %d)",
        len(pool), len(result), stats["top_score"], stats["bottom_score"],
        stats["guaranteed_slots"],
    )
    return result, stats
