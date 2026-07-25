"""jobs/campaigns/book_launch_parser.py — Deterministic (non-LLM) parser for
book-launch campaign source docs.

Splits mechanically on the source markdown's own consistent headers:
  "## WEEK N"                              -> week boundary
  "## DAY 3 POST-LAUNCH"                   -> day-3 boundary (week_number=None)
  "### FB Post N (image: ...)"             -> facebook / public
  "### Brevo Email — \"Subject\""            -> brevo / general
  "### Brevo Email (Donor segment) — \"Subject\"" -> brevo / donor
  "### ARC Reader Email — \"Subject\""       -> brevo / arc

Then cross-references each parsed item against the dated schedule table in
TWJ-Brevo-Automation-Handoff.md to attach a send_date.

No database writes. No network calls. Pure text in, structured rows out.
"""
import re
from pathlib import Path

CAMPAIGN_DIR = Path.home() / "watson" / "data" / "campaigns" / "twj"
HANDOFF_FILE = CAMPAIGN_DIR / "TWJ-Brevo-Automation-Handoff.md"
COPY_FILE = CAMPAIGN_DIR / "TWJ-Launch-Full-Copy-Weeks-1-8-CORRECTED.md"

CAMPAIGN_ID = "twj-2026"
BOOK_TITLE = "The Wrong Jesus"
LAUNCH_DATE = "2026-09-15"
START_DATE = "2026-07-22"
FRAMEWORK_WEEKS = 8

# Already sent manually before this system existed (Step 3).
ALREADY_SENT = {
    ("2026-07-22", "facebook", "public"),
    ("2026-07-23", "brevo", "general"),
    ("2026-07-24", "brevo", "donor"),
}

# Replacement copy for the Week 5 donor email (Step 4), applied before preview.
WEEK5_DONOR_REPLACEMENT = {
    "subject": "Getting ready to ship your signed copy",
    "body_text": (
        "The Wrong Jesus launches soon, and your signed copy is already on our "
        "list to go out at launch.\n\n"
        "Quick favor: if your mailing address has changed recently, would you "
        "take thirty seconds to confirm it? [Confirm your shipping address →]\n\n"
        "Thank you, as always, for the difference your partnership makes.\n\n"
        "William, on behalf of Faith Makes Sense"
    ),
}

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _to_iso(month_abbr: str, day: str, year: int = 2026) -> str:
    return f"{year:04d}-{_MONTHS[month_abbr]:02d}-{int(day):02d}"


# ── Schedule parsing ────────────────────────────────────────────────────────

def parse_schedule(text: str) -> list[dict]:
    """Parse the '## SEND SCHEDULE' markdown table into flat (date, platform,
    segment, week_number, ordinal) entries. The Day-3 row is split into its
    two constituent sends (it lists 'Facebook + Brevo' in one row)."""
    lines = text.splitlines()
    entries = []
    in_table = False
    for line in lines:
        if line.strip().startswith("| Date"):
            in_table = True
            continue
        if in_table and line.strip().startswith("|---"):
            continue
        if in_table and not line.strip().startswith("|"):
            break
        if not in_table:
            continue

        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) != 5:
            continue
        date_raw, day, platform_raw, segment_raw, content_ref = cols

        m = re.match(r"(\w{3})\s+(\d+)", date_raw)
        if not m:
            continue
        send_date = _to_iso(m.group(1), m.group(2))

        week_m = re.match(r"Week\s+(\d+)", content_ref)
        is_day3 = content_ref.startswith("Day 3")

        if is_day3:
            # This single schedule row covers both a Facebook post and a Brevo
            # email — split into two entries.
            entries.append({
                "send_date": send_date, "week_number": None,
                "platform": "facebook", "segment": "public", "ordinal": 1,
            })
            entries.append({
                "send_date": send_date, "week_number": None,
                "platform": "brevo", "segment": "general", "ordinal": 1,
            })
            continue

        week_number = int(week_m.group(1)) if week_m else None

        if "ARC Reader Email" in content_ref:
            platform, segment = "brevo", "arc"
        elif "Brevo Email (Donor segment)" in content_ref or "Donor" in segment_raw:
            platform, segment = "brevo", "donor"
        elif "Brevo Email" in content_ref:
            platform, segment = "brevo", "general"
        elif "FB Post" in content_ref:
            platform, segment = "facebook", "public"
        else:
            platform, segment = platform_raw.lower(), segment_raw.lower()

        post_m = re.search(r"FB Post\s*(\d+)", content_ref)
        ordinal = int(post_m.group(1)) if post_m else 1

        entries.append({
            "send_date": send_date, "week_number": week_number,
            "platform": platform, "segment": segment, "ordinal": ordinal,
        })

    return entries


def _schedule_lookup(schedule_entries: list[dict]) -> dict:
    """Build a {(week_number, platform, segment, ordinal): send_date} map.
    week_number=None keys are matched in arrival order for Day-3 items."""
    lookup = {}
    day3_queue = {"facebook": [], "brevo": []}
    for e in schedule_entries:
        if e["week_number"] is None:
            day3_queue[e["platform"]].append(e["send_date"])
        else:
            key = (e["week_number"], e["platform"], e["segment"], e["ordinal"])
            lookup[key] = e["send_date"]
    return lookup, day3_queue


# ── Copy doc parsing ─────────────────────────────────────────────────────────

_WEEK_HEADER_RE = re.compile(r"^## WEEK (\d+)\s*—", re.MULTILINE)
_DAY3_HEADER_RE = re.compile(r"^## DAY 3 POST-LAUNCH", re.MULTILINE)


def _split_weeks(text: str) -> list[tuple[int | None, str]]:
    """Return [(week_number_or_None, section_text), ...] split on '## WEEK N'
    and '## DAY 3 POST-LAUNCH' headers."""
    boundary_re = re.compile(r"^## (?:WEEK (\d+)\s*—.*|DAY 3 POST-LAUNCH)", re.MULTILINE)
    matches = list(boundary_re.finditer(text))
    sections = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        week_number = int(m.group(1)) if m.group(1) else None
        sections.append((week_number, text[start:end]))
    return sections


_ITEM_HEADER_RE = re.compile(r"^### (.+)$", re.MULTILINE)


def _split_items(section_text: str) -> list[tuple[str, str]]:
    """Return [(header_line, body_text), ...] split on '### ' headers within
    one week's section."""
    matches = list(_ITEM_HEADER_RE.finditer(section_text))
    items = []
    for i, m in enumerate(matches):
        header = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section_text)
        body = section_text[start:end].strip()
        # Drop a trailing '---' section-divider if present.
        body = re.sub(r"\n---\s*$", "", body).strip()
        items.append((header, body))
    return items


def _classify_item(header: str, body: str, flags: list[str]) -> dict | None:
    fb_m = re.match(r"FB Post(?:\s*(\d+))?(?:\s*—\s*(morning|evening))?\s*\(image:\s*([^)]+)\)", header)
    if fb_m:
        ordinal = int(fb_m.group(1)) if fb_m.group(1) else 1
        image_type = fb_m.group(3).strip()
        return {
            "platform": "facebook", "segment": "public", "ordinal": ordinal,
            "subject": None, "body_text": body, "image_template_type": image_type,
        }

    donor_m = re.match(r'Brevo Email \(Donor segment\) — "([^"]+)"', header)
    arc_m = re.match(r'ARC Reader Email — "([^"]+)"', header)
    general_m = re.match(r'Brevo Email — "([^"]+)"', header)

    if donor_m or arc_m or general_m:
        header_subject = (donor_m or arc_m or general_m).group(1)
        subj_m = re.search(r"^Subject:\s*(.+)$", body, re.MULTILINE)
        if not subj_m:
            flags.append(f"No 'Subject:' body line found for header {header!r} — used header text instead.")
            real_subject = header_subject
            body_only = body
        else:
            real_subject = subj_m.group(1).strip()
            if real_subject != header_subject:
                flags.append(
                    f"Header/body subject mismatch: section header says {header_subject!r}, "
                    f"actual 'Subject:' line says {real_subject!r} — used the 'Subject:' line."
                )
            body_only = body[subj_m.end():].strip()

        segment = "donor" if donor_m else "arc" if arc_m else "general"
        return {
            "platform": "brevo", "segment": segment, "ordinal": 1,
            "subject": real_subject, "body_text": body_only, "image_template_type": None,
        }

    flags.append(f"Unrecognized section header, skipped: {header!r}")
    return None


def parse_copy(text: str, flags: list[str]) -> list[dict]:
    parsed = []
    for week_number, section_text in _split_weeks(text):
        for header, body in _split_items(section_text):
            item = _classify_item(header, body, flags)
            if item is None:
                continue
            item["week_number"] = week_number
            parsed.append(item)
    return parsed


_IMAGE_PLAN_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*[^|]+\|\s*$", re.MULTILINE)


def cross_check_image_plan(copy_text: str, parsed_items: list[dict], flags: list[str]) -> None:
    """Cross-reference the 'IMAGE PLAN FOR FACEBOOK POSTS' summary table against
    the image_template_type each post's own header actually carries. The
    per-post header is treated as authoritative (it's what the deterministic
    parser uses); this only flags where the summary table disagrees with itself
    or with the per-post headers, since that's a source-doc quality issue worth
    surfacing rather than silently ignoring."""
    plan_start = copy_text.find("## IMAGE PLAN")
    plan_end = copy_text.find("## WEEK 1")
    if plan_start == -1 or plan_end == -1:
        flags.append("Could not locate the Image Plan summary table to cross-check.")
        return
    plan_text = copy_text[plan_start:plan_end]

    post_to_templates: dict[str, list[str]] = {}
    for row_m in _IMAGE_PLAN_ROW_RE.finditer(plan_text):
        template_name, used_by = row_m.group(1).strip(), row_m.group(2).strip()
        if template_name in ("Template Type", "---"):
            continue
        for post_label in [p.strip() for p in used_by.split(",")]:
            post_to_templates.setdefault(post_label, []).append(template_name)

    for post_label, templates in post_to_templates.items():
        if len(templates) > 1:
            flags.append(
                f"Image Plan summary table lists {post_label!r} under multiple template "
                f"rows {templates} — table-level inconsistency in the source doc. The "
                f"per-post header was used as the authoritative value regardless."
            )

    for item in parsed_items:
        if item["platform"] != "facebook" or not item["image_template_type"]:
            continue
        # A bare "/" inside one template name (e.g. "benefit/CTA card",
        # "date/countdown card") is a single template — only " / " (with
        # surrounding spaces) joins two genuinely distinct template names.
        if " / " in item["image_template_type"]:
            flags.append(
                f"Week {item['week_number']} Facebook post has a compound image_template_type "
                f"{item['image_template_type']!r} (its own header names two templates) — "
                f"needs a decision on whether to store as-is, split, or pick one."
            )


# ── Assembly ─────────────────────────────────────────────────────────────────

def build_preview(flags: list[str]) -> list[dict]:
    schedule_text = HANDOFF_FILE.read_text()
    copy_text = COPY_FILE.read_text()

    schedule_entries = parse_schedule(schedule_text)
    lookup, day3_queue = _schedule_lookup(schedule_entries)

    parsed_items = parse_copy(copy_text, flags)
    cross_check_image_plan(copy_text, parsed_items, flags)

    if any(item["week_number"] is None for item in parsed_items):
        flags.append(
            "Day-3 post-launch items don't fit the 1-8 week_number range in the schema "
            "(framework_weeks=8) — stored with week_number=NULL here. Needs a schema "
            "decision: NULL, week_number=9, or a separate is_bonus/day flag."
        )

    # Track per-(week, platform, segment) ordinal counters so repeat FB Post
    # slots in a week (Post 1, Post 2) line up with the schedule's ordinals.
    counters: dict = {}
    rows = []
    for item in parsed_items:
        week = item["week_number"]
        platform = item["platform"]
        segment = item["segment"]

        if week is None:
            # Day 3 item — pull the next queued date for this platform.
            queue = day3_queue.get(platform, [])
            send_date = queue.pop(0) if queue else None
            if send_date is None:
                flags.append(f"No Day-3 schedule date found for platform={platform!r}.")
        else:
            counter_key = (week, platform, segment)
            ordinal = counters.get(counter_key, 0) + 1
            counters[counter_key] = ordinal
            key = (week, platform, segment, ordinal)
            send_date = lookup.get(key)
            if send_date is None:
                flags.append(
                    f"No schedule match for week={week} platform={platform} "
                    f"segment={segment} ordinal={ordinal} — date left blank."
                )

        row = {
            "campaign_id": CAMPAIGN_ID,
            "week_number": week,
            "send_date": send_date,
            "platform": platform,
            "segment": segment,
            "subject": item["subject"],
            "body_text": item["body_text"],
            "image_template_type": item["image_template_type"],
            "status": "scheduled",
            "telegram_message_id": None,
            "sent_at": None,
        }

        # Step 4: Week 5 donor email copy replacement, applied before preview.
        if week == 5 and platform == "brevo" and segment == "donor":
            row["subject"] = WEEK5_DONOR_REPLACEMENT["subject"]
            row["body_text"] = WEEK5_DONOR_REPLACEMENT["body_text"]
            row["status"] = "edited"
            flags.append(
                "Week 5 donor email ('One week out') copy replaced per Step 4 instructions; "
                "status set to 'edited' since the schema provides that state for exactly "
                "this case — flagging in case 'scheduled' was actually intended instead."
            )

        # Step 3: mark already-sent items.
        if row["send_date"] and (row["send_date"], platform, segment) in ALREADY_SENT and week in (1, None):
            # Guard on week==1 (or Day-3/None) so only the intended 3 rows match —
            # the ALREADY_SENT set is itself scoped to specific dates already.
            row["status"] = "sent"

        rows.append(row)

    return rows


if __name__ == "__main__":
    flags: list[str] = []
    rows = build_preview(flags)

    print(f"Campaign: {BOOK_TITLE} ({CAMPAIGN_ID})")
    print(f"Launch date: {LAUNCH_DATE} | Start date: {START_DATE} | Framework weeks: {FRAMEWORK_WEEKS}")
    print(f"Total parsed sends: {len(rows)}")
    print()

    header = f"{'Wk':<4}{'Date':<12}{'Platform':<10}{'Segment':<9}{'Status':<11}{'Image Template':<26}{'Subject / First line'}"
    print(header)
    print("-" * len(header))
    for r in rows:
        wk = str(r["week_number"]) if r["week_number"] is not None else "Day3"
        date = r["send_date"] or "???"
        img = r["image_template_type"] or ""
        if r["subject"]:
            label = r["subject"]
        else:
            first_line = r["body_text"].splitlines()[0] if r["body_text"] else ""
            label = (first_line[:70] + "...") if len(first_line) > 70 else first_line
        print(f"{wk:<4}{date:<12}{r['platform']:<10}{r['segment']:<9}{r['status']:<11}{img:<26}{label}")

    print()
    if flags:
        print(f"FLAGS ({len(flags)}):")
        for f in flags:
            print(f" - {f}")
    else:
        print("No parse flags.")
