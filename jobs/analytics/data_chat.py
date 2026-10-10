"""jobs/analytics/data_chat.py — open-ended attendance / web-traffic / event-
signup / contact / pastoral-care Q&A for the Telegram team-chat path in
bot/bot.py.

Per Bill's 2026-09-02 decisions: (1) attendance and web-traffic questions
should have NOTHING off limits for team-chat users — not just the phrasings
anticipated in advance elsewhere in bot.py; (2) since only key Catalyst
leaders get Watson access at all, don't distinguish staff/elders/deacons —
every onboarded leader gets the same full access, including contact info,
via allow_contact_info (bot.py always passes True; the parameter exists so
a narrower future caller isn't required to reintroduce the plumbing). Three
domains, two separate SQLite files (attendance's file never joined with the
other two in one query):

  attendance — data/congregation.db: attendance, classroom_attendance,
               kids_checkin (added 2026-09-30, see below),
               members (name/deacon/status/campus columns always; email/
               phone/address/birthdate included only when allow_contact_info
               is True); deacon_notes, next_steps, follow_ups,
               deacon_visible_prayer_requests, deacon_visible_connect_cards
               (added 2026-09-08 per Bill's decision that deacons/leaders
               should see everything in congregation.db except his own
               private pastoral notes -- see below). notes/status_note stay
               locked regardless -- per Bill's 2026-09-02 explicit call,
               those can hold prayer-request/pastoral content well beyond
               plain contact info.
  web        — data/watson.db: engagement_sheet_metrics only.
  events     — data/watson.db: church_events, event_registrations (added
               2026-09-06 for event signup tracking — see jobs/events/).
               No contact-info gate; registrant email/phone is always
               queryable, unlike members' contact columns above.

Two privacy tiers stay off limits regardless of the 2026-09-08 widening
above, deliberately not conflated:
  - Bill's own pastoral_notes (jobs/pastoral_notes/) live in watson.db, a
    different file the attendance domain never opens -- structurally
    unreachable here, not just column-blocked. His per-note choice to
    "share:" a copy into deacon_notes (see jobs/pastoral_notes/handler.py)
    is the only way that content ever reaches this domain.
  - A prayer request a submitter flagged leadership-only
    (prayer_requests.leadership_only=1 / connect_cards.prayer_request_public=0)
    is a member-set privacy choice, not Bill's -- the deacon_visible_*
    views (jobs/congregation/migrate_deacon_visible_views.py) exclude those
    rows/columns at the SQL level, and the raw prayer_requests/connect_cards
    tables stay off the whitelist entirely so a generated query can't
    route around the view by accident.

Reuses jobs.skills.cdb_query's battle-tested pattern-match layer (Bill's own
`cdb:` skill) as a free, LLM-free fast path for the common attendance
phrasings it already recognizes — its output is still run through the same
table/column validator below, so a branch that selects a blocked PII column
(a couple of its follow-up-list branches do, fine for Bill's own chat but
not here) or an out-of-scope table just falls through to this module's own
LLM-generated query instead of being trusted blind.

For anything pattern-match doesn't recognize, a single Ollama call
(qwen2.5-coder:7b — the same model cdb_query.py uses for SQL) both
classifies the question's domain and writes the SQL in one pass. The actual
result rows are formatted directly into the reply — no second LLM pass
restates the numbers, so a wrong answer can only come from a wrong query,
never a mis-remembered one.

Safety: whatever produces the SQL, it must be exactly one SELECT statement
referencing only whitelisted tables/columns for its domain — anything else
(a second statement, a write keyword, ATTACH/PRAGMA, an out-of-scope table,
a blocked PII column) is rejected outright, never "fixed up" or retried
blind. Execution is always against a mode=ro connection as defense in depth
even if a bad query somehow slipped past validation.
"""

import logging
import os
import re
import sqlite3
import time
from datetime import date, timedelta

import requests

from config.settings import DB_PATH as _WATSON_DB_PATH
from core.claude_tier import call_claude
from core.database import get_connection
import core.llm_log  # noqa: F401 -- installs Ollama call logging, see core/llm_log.py

log = logging.getLogger(__name__)

CONGREGATION_DB_PATH = os.path.expanduser("~/watson/data/congregation.db")
WATSON_DB_PATH = str(_WATSON_DB_PATH)

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5-coder:7b"  # same model jobs/skills/cdb_query.py uses for SQL

_DB_PATH = {"attendance": CONGREGATION_DB_PATH, "web": WATSON_DB_PATH, "events": WATSON_DB_PATH}

_ALLOWED_TABLES = {
    "attendance": {
        "attendance", "classroom_attendance", "kids_checkin", "kids", "members",
        "deacon_notes", "next_steps", "follow_ups",
        "deacon_visible_prayer_requests", "deacon_visible_connect_cards",
        "team_memberships", "serving_attendance", "group_attendance", "group_counts",
    },
    # GUARDRAIL (Bill, 2026-09-26): never add sms_messages / sms_threads /
    # sms_scheduled_messages here. Watson SMS's message log is Bill's own
    # 1:1 pastoral texting record -- deliberately kept out of every
    # leader/deacon-facing surface, including this one. See
    # jobs/sms/schema.py's module docstring for the storage side of this.
    "web": {"engagement_sheet_metrics"},
    "events": {"church_events", "event_registrations"},
}

# SQLite table-valued functions, not real tables -- they only ever operate
# on whatever string is passed as their argument (an already-allowed
# column, per the extra_fields-unpacking guidance in _EVENTS_SCHEMA), never
# reach outside the domain's real tables on their own. Allowed in every
# domain's FROM/JOIN so _validate_sql's table-allowlist check below doesn't
# reject them as an unrecognized table.
_ALWAYS_ALLOWED_TABLES = {"json_each", "json_tree"}

# Per Bill's 2026-09-02 follow-ups ("only key leaders get Watson access, we
# don't need to worry too much about access" -> "allow birthdates, keep
# notes locked"), email/phone/address/birthdate are allowed for team-chat
# (allow_contact_info=True) -- toggled per-caller in bot.py, not a blanket
# unblock. notes stays blocked unconditionally -- explicitly kept locked
# since it can hold pastoral/prayer content well beyond contact info.
#
# 2026-09-24 Phase 6: status/member_status/partnership_status/deacon_status/
# status_reason/status_since/status_note/snowbird_return columns dropped
# entirely (see ~/.claude/plans/zesty-cuddling-robin.md) -- partner/active
# are exposed below (not sensitive on their own); residency stays blocked,
# same reasoning snowbird_return had, it reveals whether someone is
# non-local/seasonal. carrier removed from this set earlier (grid cleanup,
# see migrate_catalystdb_grid_cleanup.py) -- that column's gone too.
_CONTACT_COLUMN_WORDS = {"email", "phone", "address", "birthdate"}
_ALWAYS_BLOCKED_COLUMN_WORDS = {"notes", "residency", "ssn"}
# household_id/household_role (added 2026-09-12, see
# jobs/congregation/migrate_household_role.py) are deliberately NOT in the
# blocked set above -- they're the whole
# point of the spouse/child/parent self-join examples below, and they carry
# no contact-info meaning of their own (no address, no phone), so leaving
# them queryable doesn't need allow_contact_info either.


def _attendance_schema(allow_contact_info: bool) -> str:
    contact_cols = ", email TEXT, phone TEXT, address TEXT, birthdate TEXT" if allow_contact_info else ""
    return (
        "attendance(member_id INTEGER, service_date TEXT, campus TEXT)\n"
        "  -- one row per person per service actually attended. campus is 'Wilmington' or 'Online'.\n"
        "classroom_attendance(date TEXT, kids_nursery, adults_nursery, kids_toddlers, adults_toddlers, kids_prek, adults_prek, kids_elementary, adults_elementary INTEGER)\n"
        "  -- one row per Sunday with headcounts for each of the 4 kids' classrooms, staff-tallied from a Google Sheet.\n"
        "kids_checkin(kid_id INTEGER, event_date TEXT, class_name TEXT, campus TEXT)\n"
        "  -- one row per kid per classroom check-in, real per-child data from the Subsplash check-in app (2025-01-26\n"
        "  -- onward only -- no rows before that). campus is usually NULL, not captured per check-in. For a kids\n"
        "  -- headcount on a given Sunday, COUNT(DISTINCT kid_id) WHERE event_date = that date -- prefer this table\n"
        "  -- over classroom_attendance whenever it has any row for the date (real data), falling back to\n"
        "  -- classroom_attendance's staff-tallied headcount only for dates this table doesn't cover at all.\n"
        "kids(id INTEGER, first_name TEXT, last_name TEXT, gender TEXT, current_class TEXT, household_id TEXT)\n"
        "  -- one row per kid (added 2026-10-01). A kid's real attendance is ALWAYS in kids_checkin (join on\n"
        "  -- kids_checkin.kid_id = kids.id) -- never in the `attendance` table, even though every kid also has a\n"
        "  -- mirrored `members` row for CatalystDB display (that row never gets attendance rows written to it).\n"
        "  -- current_class is the kid's most recent classroom from their latest check-in. A \"which kids were in\n"
        "  -- [class]\" or \"when did we last see [kid]\" question means querying kids/kids_checkin, not members/attendance.\n"
        f"members(id INTEGER, name TEXT, deacon TEXT, campus_preference TEXT, first_visit_date TEXT, active TEXT, partner TEXT, household_id TEXT, household_role TEXT, gender TEXT, started_serving_date TEXT{contact_cols})\n"
        "  -- started_serving_date is when that person began serving/volunteering (banquet length-of-service\n"
        "  -- tracking) -- NULL for anyone who isn't a serving volunteer. A question about how LONG someone has\n"
        "  -- been serving (not just the raw date) means computing tenure in the SQL itself, the same way age is\n"
        "  -- computed from birthdate, e.g.\n"
        "  -- `CAST((julianday('now') - julianday(started_serving_date)) / 365.25 AS INTEGER) AS years_serving` --\n"
        "  -- never just return the bare date for a \"how long\"/\"length of service\" question.\n"
        "  -- deacon holds the free-text NAME of the deacon shepherding that member -- \"who's in <X>'s deacon group\" means WHERE deacon LIKE '%X%' DIRECTLY.\n"
        "  -- Never look up X's own row and reuse ITS deacon value instead -- deacons/elders themselves are tagged with a\n"
        "  -- leadership bucket there (e.g. 'Elders & Deacons'), shared by every deacon/elder and their spouse, not their own\n"
        "  -- name -- reusing it returns that whole leadership bucket, a wrong and unrelated group, not the person's shepherded members.\n"
        "  -- join attendance.member_id = members.id for a specific person's or group's attendance.\n"
        "  -- household_id groups members of the same family (e.g. 'H047'); household_role is one of 'husband', 'wife',\n"
        "  -- 'widow', 'widower' (a surviving spouse -- their deceased spouse may still be on file, active='deceased'),\n"
        "  -- 'head' (a single parent -- no spouse on file), 'child', 'other', or NULL if never recorded. gender is\n"
        "  -- 'male'/'female'/NULL, set automatically whenever a husband/wife role is assigned. To find X's SPOUSE:\n"
        "  -- self-join members to itself on matching household_id (excluding X's own row), requiring household_role\n"
        "  -- IN ('husband','wife','widow','widower') on BOTH sides -- see the example below (a 'head' has no spouse\n"
        "  -- by definition, so never include 'head' in a spouse lookup). To find X's CHILDREN: same self-join but the\n"
        "  -- other side's household_role = 'child' (no requirement on X's own role). To find a CHILD's PARENTS: same\n"
        "  -- self-join with X's own household_role = 'child' and the other side's household_role IN\n"
        "  -- ('husband','wife','widow','widower','head') -- a\n"
        "  -- single parent's kids still need to find their one parent. Never use household_id alone (matching last\n"
        "  -- name or address) to answer a spouse/parent/child question -- siblings and parent/child pairs can share a\n"
        "  -- household_id too, and household_role is what actually distinguishes the relationship.\n"
        "  -- partner is a category, one of exactly 'partner', 'np' -- to filter to just partners use\n"
        "  -- partner = 'partner', NEVER partner IS NOT NULL (that matches everyone, the column is always populated).\n"
        "  -- active is a category, one of exactly 'active', 'non-active', 'disconnected', 'deceased' -- to filter\n"
        "  -- to the normal/current roster (excluding only disconnected/deceased -- non-active members, meaning 8+\n"
        "  -- weeks absent, still count as part of the roster for this purpose) use\n"
        "  -- active NOT IN ('disconnected', 'deceased').\n"
        "deacon_notes(member_id INTEGER, note TEXT, status TEXT, created_at TEXT, author_deacon TEXT)\n"
        "  -- a deacon's own logged follow-up note about a member. status is 'open' or resolved/closed. join member_id = members.id.\n"
        "next_steps(member_id INTEGER, step TEXT, date TEXT)\n"
        "  -- a next-step/action item recorded for a member (e.g. from a connect card). join member_id = members.id.\n"
        "follow_ups(member_id INTEGER, note TEXT, status TEXT, created_at TEXT)\n"
        "  -- a general follow-up task tied to a member, separate from deacon_notes. status is 'open' or resolved/closed. join member_id = members.id.\n"
        "deacon_visible_prayer_requests(member_id INTEGER, request_text TEXT, date TEXT, created_at TEXT)\n"
        "  -- a member's prayer request. Use this view, NEVER the raw prayer_requests table (not queryable -- it also holds requests\n"
        "  -- the submitter marked leadership-only, which this view already excludes). join member_id = members.id.\n"
        "deacon_visible_connect_cards(member_id INTEGER, service_date TEXT, campus TEXT, questions_comments TEXT, next_steps TEXT, is_first_visit INTEGER, prayer_request TEXT)\n"
        "  -- a submitted connect card. Use this view, NEVER the raw connect_cards table (not queryable -- its prayer_request column\n"
        "  -- can hold non-public content this view already nulls out). is_first_visit=1 means it was that person's first visit. join member_id = members.id.\n"
        "team_memberships(id INTEGER, member_id INTEGER, team_name TEXT, position TEXT)\n"
        "  -- which volunteer/serving team(s) a member is on (Nursery, Worship Team, Security, Deacons, etc, from the\n"
        "  -- Team Members List Export, added 2026-09-22) -- NOT the same thing as members.deacon (who SHEPHERDS a\n"
        "  -- member) or leadership_roles (staff/elder/deacon office). position is their role WITHIN that team (e.g.\n"
        "  -- 'Nursery Caretaker', 'Vocalist') and can be NULL. join member_id = members.id. Team names are\n"
        "  -- inconsistently suffixed (\"WORSHIP TEAM\" but plain \"NURSERY\", \"DEACONS\", \"SECURITY\") so always match\n"
        "  -- with team_name LIKE '%partial%', never exact equality, using whatever phrase the asker used.\n"
        "serving_attendance(id INTEGER, member_id INTEGER, team_name TEXT, service_date TEXT)\n"
        "  -- who actually SERVED on their team on a given Sunday (staff check this off weekly at /cat/serving, from\n"
        "  -- 2026-09-20). One row per member+team+service_date served; no row = did not serve (or not yet checked off).\n"
        "  -- Differs from team_memberships (the roster) and from attendance (who was AT church). join member_id =\n"
        "  -- members.id; match team_name with LIKE '%partial%' like team_memberships.\n"
        "group_attendance(id INTEGER, series TEXT, event_date TEXT, member_id INTEGER)\n"
        "  -- who attended a small group / special event session (Men's Fraternity, Woven, Remix, Men's Breakfast, etc),\n"
        "  -- checked off by group leaders at /cat/groups (from 2026-10-07). series looks like 'Small Groups|Men''s Fraternity\n"
        "  -- Bible Study' or 'Special Events|The Names of God' -- always match with series LIKE '%partial%'. Weekly Sunday groups not on the calendar (Jim & Lisa's Group, Remix Sunday Morning Group, Shift Young Adult Group, 9am Elementary Kids Group) are 'Small Groups|<title>'. join member_id =\n"
        "  -- members.id. No row = did not attend (or not yet recorded). Celebrate Recovery is NEVER recorded by name.\n"
        "group_counts(series TEXT, event_date TEXT, guests INTEGER, headcount INTEGER)\n"
        "  -- per-session non-member guest count (guests) for named groups, and the head count (headcount) for Celebrate\n"
        "  -- Recovery, which is head count only. Total people at a named session = group_attendance rows + guests."
    )

_WEB_SCHEMA = """
engagement_sheet_metrics(tab TEXT, section TEXT, metric_label TEXT, month TEXT, value_numeric REAL, value_raw TEXT)
  -- month is 'YYYY-MM-01', one row per metric per month. section/metric_label pairs:
  --   'E Mails/Website': New Web Users, Active Web Users, Avg Engagement Time (sec), Event Count, Email Campaigns Sent, Emails Opened, Email Links Clicked, Total Emails Sent
  --   'Aquisitions': Direct Link, Organic Search, Social/Referrals
  --   'Social Media': Facebook Post Likes, Facebook Post Shares, Facebook Followers, Total Facebook Posts, Instagram Post Likes, Instagram Post Shares, Instagram Followers, Total Instagram Posts
  --   'Catalyt App Engagement' (sic, matches the sheet as-is): App Downloads, App Impressions, App Launches
  --   'Top Page Views': metric_label is 'Top Page 1'..'Top Page 5', value_raw is the page name, value_numeric is its share (0-1)
""".strip()

_EVENTS_SCHEMA = """
church_events(id INTEGER, event_name TEXT, start_date TEXT, end_date TEXT, event_time TEXT, description TEXT, tracking_active INTEGER, rsvp_tracking INTEGER)
  -- one row per church event (picnic, retreat, class, etc). tracking_active=1 means Watson is still auto-attaching new signups to it.
  -- rsvp_tracking=1 (added 2026-09-24) means this event's registrations carry a real rsvp_status yes/no (see
  -- event_registrations below) instead of every registration meaning "attending" by default.
  -- start_date can be an empty string if the event was created via Telegram before a date was set (Kaci/Bill can only add
  -- one, may add the other later) -- treat '' the same as "no date yet", never as a real date. event_time is free text
  -- ("6:00pm - 8:00pm") and can likewise be NULL/empty if not set yet.
event_registrations(id INTEGER, event_id INTEGER, first_name TEXT, last_name TEXT, email TEXT, phone TEXT, ticket_type TEXT, num_tickets INTEGER, rsvp_status TEXT, child_count INTEGER, extra_fields TEXT, submitted_at TEXT, source TEXT)
  -- one row per person/registration for an event. num_tickets is how many people that single registration covers -- SUM(num_tickets), not COUNT(*), for "how many people are coming".
  -- join event_registrations.event_id = church_events.id for a specific event's signups. source is 'csv_import', 'email', or 'manual'.
  -- rsvp_status (added 2026-09-24, e.g. for the annual Servant Leaders Banquet) is 'yes', 'no', or NULL. NULL means
  -- this event doesn't use RSVP tracking (an ordinary signup/ticket event, e.g. the picnic) -- treat NULL rows as
  -- attending, same as before this column existed. When rsvp_status IS NOT NULL, a headcount question ("how many
  -- people are coming") must filter `rsvp_status = 'yes'` -- a 'no' row is someone who declined, NOT an attendee,
  -- and must never be included in SUM(num_tickets). child_count (added 2026-09-24) is a SEPARATE headcount of
  -- children needing paid childcare for that event -- it is NOT part of num_tickets and must never be added to it;
  -- a childcare/children question sums child_count, a meal/adult/"how many people" question sums num_tickets
  -- (both filtered to rsvp_status = 'yes' when that column is in use), and these two totals are always reported
  -- separately, never combined into one number.
  -- extra_fields holds any custom sign-up-form question(s) for that event as a JSON object string, e.g.
  -- {"Please choose to bring a side dish or dessert:": "Dessert"}. Different events have different custom
  -- questions/answers (or none -- extra_fields is NULL/empty for a plain registration), so never assume a key
  -- name; match on the ANSWER text instead. IMPORTANT: the question text itself often contains the same words
  -- as its possible answers (e.g. the key above literally contains the word "dessert"), so a bare
  -- `LIKE '%Dessert%'` matches that key too and silently counts EVERYONE who answered the question, not just
  -- the ones who picked Dessert. Always wrap the answer in the JSON quoting to match the VALUE only:
  -- `extra_fields LIKE '%"Dessert"%'` (the literal double-quote characters around the word, escaped for SQL as
  -- '' if needed) -- this matches only a quoted JSON value, never a key's sentence. Use this whenever a
  -- question asks about a specific signup-form choice (what they're bringing, a session/group they picked, a
  -- t-shirt size, etc), not just num_tickets.
""".strip()

_SYSTEM_TEMPLATE = """You are a SQL query generator for a church's internal Telegram assistant. \
Decide whether the question falls into the ATTENDANCE domain, the WEB domain, the EVENTS domain, or none. \
ATTENDANCE covers not just worship-service/classroom attendance counts but ANY question about a member's own \
record in the members table below -- contact info, birthdate, their deacon/group, status -- since that table \
lives in the same domain, AND ANY pastoral/care question about a member: deacon notes logged about them, their \
prayer requests, next steps, or follow-ups, or what they wrote on a connect card. WEB covers social-media/website \
traffic metrics. EVENTS covers signups/registrations/\
RSVPs/tickets for a specific church event (picnic, retreat, class, etc) -- who's registered, headcounts, ticket \
counts, contact info for a registrant. If it's one of those three, write ONE single-line read-only SQLite \
SELECT statement that answers it exactly, using ONLY the tables and columns listed below -- never invent a \
table or column, never write anything but SELECT. If the question has no month/date range and asks for a \
current or total count ("how many X do we have", "what's our X"), use the single most recent row (ORDER BY \
month DESC LIMIT 1 for web metrics) rather than every historical row. \
When matching a person's name (members.name or members.deacon), NEVER use exact equality (=) -- the asker's \
spelling may drop punctuation, get plural/typo'd, or vary in case. Use `LIKE '%Full Name%'` with the WHOLE \
name as given (first and last together, case-insensitive by default in SQLite, punctuation/trailing letters \
just fall outside the %...% wildcard) so "bill crooks" or "Bill Crook's" still matches the stored name \
"Bill Crook" -- do NOT reduce the match to just the last name, since spouses/relatives sharing a surname \
(e.g. "Tara Mathena" and "Dino Mathena") would then wrongly match each other too. Whenever a query matches a \
person this way, always SELECT their name column alongside whatever was asked for, so an unexpected multi-\
match is still attributable to a specific person rather than an unlabeled list of values. The same LIKE rule \
applies to church_events.event_name -- match on whatever partial name the asker used ("the picnic" -> \
event_name LIKE '%picnic%'). When the answer is a LIST OF PEOPLE (e.g. "who's registered", "who signed up", \
"who is in X's group"), do NOT return separate raw columns like first_name/last_name/num_tickets side by side \
-- concatenate them with SQL string concatenation into ONE readable text column instead (e.g. \
`first_name || ' ' || last_name || CASE WHEN num_tickets > 1 THEN ' (' || num_tickets || ' tickets)' ELSE '' END`), \
so each row reads as a single natural line rather than a raw field dump. \
A LIST of event_registrations must always include ALL available data Watson has for each person, never just \
name/tickets -- if extra_fields is set for that row, append what they answered on the custom sign-up-form \
question too, e.g. `... || CASE WHEN extra_fields IS NOT NULL AND extra_fields != '' THEN ' — ' || \
(SELECT group_concat(je.value, ', ') FROM json_each(event_registrations.extra_fields) je) ELSE '' END`. This \
applies even when the question asks to be filtered down to one specific answer ("who's bringing dessert") -- \
filter the WHERE clause on that answer (see extra_fields matching rule below) but still show each matched \
person's full row (name, tickets, and their extra_fields answer), not just their name. \
When a question asks for a headcount "on both campuses" or "across both campuses" for a SINGLE service/date \
(e.g. "how many attended on both campuses this past Sunday"), that means the COMBINED total across Wilmington \
and Online for that one date -- COUNT(DISTINCT member_id) with no campus filter, never \
`GROUP BY member_id HAVING COUNT(DISTINCT campus) = 2`, which asks whether one person attended two campuses at \
the SAME service (structurally impossible for a single Sunday, always returns zero). That per-person \
"attended both" grouping only makes sense for a genuine multi-week hybrid-attendance question spanning several \
services, not a single Sunday's headcount.
Today's date is {today}.

ATTENDANCE tables (file: congregation.db):
{attendance_schema}

WEB tables (file: watson.db -- a different file, never mixed with attendance tables in one query):
{web_schema}

EVENTS tables (file: watson.db -- never mixed with attendance/congregation.db tables in one query):
{events_schema}

Reply with EXACTLY this format and nothing else:
DOMAIN: attendance|web|events|none
SQL: <single-line SELECT -- omit this line entirely if DOMAIN is none>

Q: what is the average attendance for the last four weeks?
DOMAIN: attendance
SQL: SELECT AVG(cnt) FROM (SELECT service_date, COUNT(*) AS cnt FROM attendance GROUP BY service_date ORDER BY service_date DESC LIMIT 4)

Q: how many people attended church on both campuses this past Sunday?
DOMAIN: attendance
SQL: SELECT COUNT(DISTINCT member_id) FROM attendance WHERE service_date = (SELECT MAX(service_date) FROM attendance)

Q: how many app downloads did we get in August?
DOMAIN: web
SQL: SELECT value_raw FROM engagement_sheet_metrics WHERE section = 'Catalyt App Engagement' AND metric_label = 'App Downloads' AND month LIKE '2026-08%'

Q: how many facebook followers do we have?
DOMAIN: web
SQL: SELECT value_raw FROM engagement_sheet_metrics WHERE section = 'Social Media' AND metric_label = 'Facebook Followers' ORDER BY month DESC LIMIT 1

Q: how is Jim Bouchat's group doing for attendance?
DOMAIN: attendance
SQL: SELECT COUNT(*) FROM attendance WHERE member_id IN (SELECT id FROM members WHERE deacon LIKE '%Jim Bouchat%') AND service_date >= date('now', '-4 weeks')

Q: who is in bill crooks deacon group?
DOMAIN: attendance
SQL: SELECT name FROM members WHERE deacon LIKE '%Bill Crook%'

Q: which partners haven't been assigned to a deacon yet?
DOMAIN: attendance
SQL: SELECT name FROM members WHERE partner = 'partner' AND deacon = '--'

Q: who is Kaci Gravatt's spouse?
DOMAIN: attendance
SQL: SELECT m2.name FROM members m1 JOIN members m2 ON m2.household_id = m1.household_id AND m2.id != m1.id WHERE m1.name LIKE '%Kaci Gravatt%' AND m1.household_role IN ('husband','wife') AND m2.household_role IN ('husband','wife')

Q: who is Kaci Gravatt's husband?
DOMAIN: attendance
SQL: SELECT m2.name FROM members m1 JOIN members m2 ON m2.household_id = m1.household_id AND m2.id != m1.id WHERE m1.name LIKE '%Kaci Gravatt%' AND m2.household_role = 'husband'

Q: who are Tara Mathena's children?
DOMAIN: attendance
SQL: SELECT m2.name FROM members m1 JOIN members m2 ON m2.household_id = m1.household_id AND m2.id != m1.id WHERE m1.name LIKE '%Tara Mathena%' AND m2.household_role = 'child'

Q: who are Kathryn Taylor's parents?
DOMAIN: attendance
SQL: SELECT m2.name FROM members m1 JOIN members m2 ON m2.household_id = m1.household_id AND m2.id != m1.id WHERE m1.name LIKE '%Kathryn Taylor%' AND m1.household_role = 'child' AND m2.household_role IN ('husband','wife','head')

Q: what deacon notes have been logged about Barry Balderson?
DOMAIN: attendance
SQL: SELECT dn.note, dn.status, dn.author_deacon, dn.created_at FROM deacon_notes dn JOIN members m ON m.id = dn.member_id WHERE m.name LIKE '%Barry Balderson%' ORDER BY dn.created_at DESC

Q: what are the open prayer requests for Jim Bouchat's group?
DOMAIN: attendance
SQL: SELECT m.name, pr.request_text, pr.date FROM deacon_visible_prayer_requests pr JOIN members m ON m.id = pr.member_id WHERE m.deacon LIKE '%Jim Bouchat%' ORDER BY pr.date DESC

Q: are there any open follow-ups for my group?
DOMAIN: attendance
SQL: SELECT m.name, f.note FROM follow_ups f JOIN members m ON m.id = f.member_id WHERE m.deacon LIKE '%Bill Crook%' AND f.status = 'open'

Q: how many people have signed up for the picnic?
DOMAIN: events
SQL: SELECT COALESCE(SUM(r.num_tickets), 0) FROM event_registrations r JOIN church_events e ON e.id = r.event_id WHERE e.event_name LIKE '%picnic%'

Q: who's registered for the picnic so far?
DOMAIN: events
SQL: SELECT r.first_name || ' ' || r.last_name || CASE WHEN r.num_tickets > 1 THEN ' (' || r.num_tickets || ' tickets)' ELSE '' END || CASE WHEN r.extra_fields IS NOT NULL AND r.extra_fields != '' THEN ' — ' || (SELECT group_concat(je.value, ', ') FROM json_each(r.extra_fields) je) ELSE '' END AS registrant FROM event_registrations r JOIN church_events e ON e.id = r.event_id WHERE e.event_name LIKE '%picnic%'

Q: how many people signed up to bring dessert for the picnic?
DOMAIN: events
SQL: SELECT COALESCE(SUM(r.num_tickets), 0) FROM event_registrations r JOIN church_events e ON e.id = r.event_id WHERE e.event_name LIKE '%picnic%' AND r.extra_fields LIKE '%"Dessert"%'

Q: who's bringing a side dish to the picnic?
DOMAIN: events
SQL: SELECT r.first_name || ' ' || r.last_name || CASE WHEN r.num_tickets > 1 THEN ' (' || r.num_tickets || ' tickets)' ELSE '' END || ' — ' || (SELECT group_concat(je.value, ', ') FROM json_each(r.extra_fields) je) AS registrant FROM event_registrations r JOIN church_events e ON e.id = r.event_id WHERE e.event_name LIKE '%picnic%' AND r.extra_fields LIKE '%"Side Dish"%'

Q: who has been serving the longest?
DOMAIN: attendance
SQL: SELECT name, started_serving_date, CAST((julianday('now') - julianday(started_serving_date)) / 365.25 AS INTEGER) AS years_serving FROM members WHERE started_serving_date IS NOT NULL ORDER BY started_serving_date ASC LIMIT 10

Q: who's on the worship team?
DOMAIN: attendance
SQL: SELECT m.name FROM team_memberships tm JOIN members m ON m.id = tm.member_id WHERE tm.team_name LIKE '%worship%' AND m.active = 1 ORDER BY m.name

Q: what team is Gary Tabor on?
DOMAIN: attendance
SQL: SELECT m.name, (SELECT group_concat(team_name, ', ') FROM team_memberships WHERE member_id = m.id) AS teams FROM members m WHERE m.name LIKE '%Gary Tabor%' AND m.active = 1
{contact_example}"""

_CONTACT_ALLOWED_EXAMPLE = """
Q: what's Kaci's phone number?
DOMAIN: attendance
SQL: SELECT name, phone FROM members WHERE name LIKE '%Kaci%'

Q: when is Tara Mathena's birthday?
DOMAIN: attendance
SQL: SELECT name, birthdate FROM members WHERE name LIKE '%Tara Mathena%'
"""

_CONTACT_BLOCKED_EXAMPLE = """
Q: what's Kaci's phone number?
DOMAIN: none
"""

# Found 2026-09-02 testing: passing asker_name only via the system prompt's
# "the person asking is named X" context is NOT reliable -- qwen2.5-coder:7b
# repeatedly ignored it and hallucinated an unrelated name (e.g. Jim Bouchat
# asking "how is my group doing" produced `deacon = 'Kaci'`, someone else
# entirely, with no error and a confident-looking answer). Substituting the
# literal name into the question text itself before generation is far more
# reliable -- the model only has to read a concrete name out of the
# question, not resolve an abstract pronoun using separately-supplied
# context it's free to ignore.
_FIRST_PERSON_RE = re.compile(r"\b(my|our|mine|ours)\b", re.IGNORECASE)
_FIRST_PERSON_SUBJECT_RE = re.compile(r"\bI\b")


def _resolve_first_person(question: str, asker_name: str) -> str:
    q = _FIRST_PERSON_RE.sub(f"{asker_name}'s", question)
    q = _FIRST_PERSON_SUBJECT_RE.sub(asker_name, q)
    return q


_NAME_COLUMN_LOOKUP_RE = re.compile(r"\bname\s+like\b", re.IGNORECASE)

# Found 2026-09-11 testing the clarify fix live: Tyler got asked "which Bill
# did you mean?", replied "Crook", and that one-word reply fell all the way
# through to general chat ("what's on your mind regarding the term
# 'crook'?") because nothing connected it back to the pending question --
# each call into this module is otherwise stateless. This in-process cache
# remembers, per asker, the candidate rows a clarifying question was just
# asked about, so a short follow-up naming one of them can be resolved
# directly instead of re-running the whole question through SQL generation
# (which has no verb/question shape to work with for a bare name reply).
# Lost on a bot restart -- acceptable, worst case the leader just asks again.
# Scoped per asker_name (each leader's pending clarification is independent
# of everyone else's) and bounded by _PENDING_CLARIFICATION_MAX_ENTRIES --
# per Bill's 2026-09-11 direction, an asker who never sends a follow-up
# would otherwise sit here forever; a size cap with oldest-first eviction
# keeps this from growing unbounded without needing a background sweeper.
_PENDING_CLARIFICATION_TTL_SECONDS = 300
_PENDING_CLARIFICATION_MAX_ENTRIES = 200
_pending_clarifications: dict[str, dict] = {}

_NAME_WORD_RE = re.compile(r"[a-z]+")


def _remember_pending_clarification(asker_name: str, rows: list[dict], name_key: str) -> None:
    _pending_clarifications[asker_name] = {"rows": rows, "name_key": name_key, "asked_at": time.monotonic()}
    if len(_pending_clarifications) > _PENDING_CLARIFICATION_MAX_ENTRIES:
        oldest_asker = min(_pending_clarifications, key=lambda k: _pending_clarifications[k]["asked_at"])
        _pending_clarifications.pop(oldest_asker, None)


def _forget_pending_clarification(asker_name: str) -> None:
    _pending_clarifications.pop(asker_name, None)


# --- "Which one do you mean: <event A> or <event B>?" follow-ups (2026-10-06): Bill answered "Men's Frat Bible Study" and got "That didn't turn up
# any matching data" because nothing connected his answer to the question just asked. Same per-asker slot, TTL and size cap as the person
# clarifications above (one pending question per asker); entries carry kind="event".
_EVENT_PICK_FILLER = {"the", "one", "event", "please", "that", "this", "i", "mean", "meant", "want", "its", "it's", "is", "for", "about",
                      "um", "uh", "yes", "yeah", "yep", "option", "number", "choice", "pick", "go", "with"}
_EVENT_PICK_ORDINALS = {"first": 0, "1st": 0, "1": 0, "second": 1, "2nd": 1, "2": 1, "third": 2, "3rd": 2, "3": 2}
_EVENT_PICK_CANCEL_RE = re.compile(r"^\W*(neither|never\s*mind|nevermind|cancel|forget it|none|no)\W*$", re.I)


def _remember_pending_event_choice(asker_name: str, question: str, phrase: str, titles: list[str]) -> None:
    _pending_clarifications[asker_name] = {"kind": "event", "question": question, "phrase": phrase, "titles": list(titles), "asked_at": time.monotonic()}
    if len(_pending_clarifications) > _PENDING_CLARIFICATION_MAX_ENTRIES:
        oldest_asker = min(_pending_clarifications, key=lambda k: _pending_clarifications[k]["asked_at"])
        _pending_clarifications.pop(oldest_asker, None)


def _event_pick(reply: str, titles: list[str]) -> list[int]:
    """Indexes of the offered `titles` a short reply selects: an ordinal ("the second one", "2") or words that all belong to exactly the title
    ("Men's Frat Bible Study", "billiards"). [] if the reply names none of them (so it is a new question, not an answer)."""
    norm = re.sub(r"\bfrat\b", "fraternity", (reply or "").lower().replace("\u2019", "'"))
    words = re.findall(r"[a-z0-9']+", norm)
    ords = [w for w in words if w in _EVENT_PICK_ORDINALS]
    if ords and len(words) <= 5 and not (set(words) - set(_EVENT_PICK_ORDINALS) - _EVENT_PICK_FILLER):
        i = _EVENT_PICK_ORDINALS[ords[0]]
        return [i] if i < len(titles) else []
    try:
        from jobs.events.pattern_match import _tokens
    except Exception:
        return []
    pt = _tokens(" ".join(w for w in words if w not in _EVENT_PICK_FILLER))
    if not pt:
        return []
    return [i for i, t in enumerate(titles) if pt <= _tokens(t)]


def _question_with_event(original: str, phrase: str, title: str) -> str:
    """The original question with the ambiguous event phrase replaced by the chosen title ("... for men's fraternity tomorrow night" ->
    "... for Men's Fraternity Bible Study tomorrow night"); if the phrase cannot be found, the title is appended as the thing asked about."""
    norm = re.sub(r"\bfrat\b", "fraternity", original, flags=re.I)
    if phrase:
        new, n = re.subn(re.escape(phrase), lambda m: title, norm, count=1, flags=re.I)
        if n:
            return new
    return f"{original.rstrip(' ?.!')} for {title}"


# Per-leader rolling conversation buffer -- see
# notes/team_chat_conversational_memory_spec.md. Both this module's own LLM
# calls (_generate, below) and
# bot.py's _get_team_reply_sync draw on the same buffer, keyed by the same
# asker_name identity already used by _pending_clarifications above, so a
# leader's whole exchange with Watson (data questions AND general chat) reads
# as one continuous conversation instead of two disconnected stateless paths.
# Same shape/reasoning as _pending_clarifications: in-process only, lost on a
# bot restart (acceptable -- worst case Watson just doesn't remember a stale
# conversation), TTL'd so a leader who goes quiet doesn't have old context
# resurface hours later, and size-capped with oldest-first eviction so an
# asker who never returns doesn't sit here forever.
_CONVERSATION_TTL_SECONDS = 1800
_CONVERSATION_MAX_TURNS = 8  # (user, assistant) messages kept, most-recent-last
_CONVERSATION_MAX_ASKERS = 200
_conversation_buffers: dict[str, dict] = {}


def get_conversation_turns(asker_name: str) -> list[dict]:
    """Returns the buffered prior turns for `asker_name` as a
    [{"role": "user"|"assistant", "content": str}, ...] list ready to prepend
    to an Ollama/Claude messages array -- empty if there's no buffer yet or
    it's gone stale past _CONVERSATION_TTL_SECONDS."""
    buf = _conversation_buffers.get(asker_name)
    if not buf:
        return []
    if time.monotonic() - buf["last_at"] > _CONVERSATION_TTL_SECONDS:
        _conversation_buffers.pop(asker_name, None)
        return []
    return list(buf["turns"])


def remember_conversation_turn(asker_name: str, role: str, content: str) -> None:
    """Appends one turn (role: "user" or "assistant") to `asker_name`'s
    buffer. Called once per side of an exchange -- see bot.py's
    compute_team_chat_reply, which calls this for both the incoming message
    and the reply actually sent, right after logging each to telegram_log."""
    if not content:
        return
    buf = _conversation_buffers.setdefault(asker_name, {"turns": [], "last_at": time.monotonic()})
    if time.monotonic() - buf["last_at"] > _CONVERSATION_TTL_SECONDS:
        buf["turns"] = []  # stale thread -- start fresh rather than splicing old context back in
    buf["turns"].append({"role": role, "content": content})
    buf["turns"] = buf["turns"][-_CONVERSATION_MAX_TURNS:]
    buf["last_at"] = time.monotonic()
    if len(_conversation_buffers) > _CONVERSATION_MAX_ASKERS:
        oldest_asker = min(_conversation_buffers, key=lambda k: _conversation_buffers[k]["last_at"])
        if oldest_asker != asker_name:
            _conversation_buffers.pop(oldest_asker, None)


# Durable per-leader role/self-description note -- the v2 idea flagged (not
# built) in notes/team_chat_conversational_memory_spec.md §6: Kaci's "I
# handle digital communications and event registrations for Catalyst" only
# lived in the 30-minute rolling buffer above, so it was gone by her next
# real conversation days later. This survives restarts and the buffer's TTL
# by living in watson.db instead of in-process memory -- one row per leader,
# overwritten (not appended) each time a new self-description is heard, since
# the goal is "what does Watson currently know about this person," not a
# growing transcript.
#
# Deliberately auto-saved with no confirm-before-save round trip (the spec's
# §6 "probably a confirm step" caveat) -- a leader's own plainly-stated
# description of their own role is low-stakes, and adding a Yes/No
# confirmation round trip for it would be a second Telegram exchange just to
# store a sentence the leader already chose to say to Watson.
_ROLE_STATEMENT_RE = re.compile(
    r"\b(i handle|i work (?:on|in|with)|i manage|i(?:'m| am)\s+(?:the|in charge of|responsible for)|"
    r"my (?:role|job) is|i (?:run|lead|oversee|coordinate))\b",
    re.IGNORECASE,
)
_LEADER_NOTE_MAX_CHARS = 300


def _bootstrap_leader_notes() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leader_notes (
                asker_name TEXT PRIMARY KEY,
                note       TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


_bootstrap_leader_notes()


def get_leader_note(asker_name: str) -> str | None:
    """The most recent self-description `asker_name` has given Watson, if
    any -- None if they've never said anything that matched
    _ROLE_STATEMENT_RE."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT note FROM leader_notes WHERE asker_name = ?", (asker_name,)
        ).fetchone()
    return row["note"] if row else None


def leader_note_context(asker_name: str) -> str:
    """A ready-to-append system-prompt suffix carrying `asker_name`'s durable
    note, or "" if there isn't one -- both _generate below and bot.py's
    _get_team_reply_sync append this to their system message so the model
    still knows a leader's stated role even after the rolling conversation
    buffer above has gone stale or the bot has restarted."""
    note = get_leader_note(asker_name)
    if not note:
        return ""
    return f"\n\nWhat {asker_name} has told you about their role, from an earlier conversation: {note}"


def maybe_learn_leader_note(asker_name: str, text: str) -> None:
    """Saves `text` as asker_name's durable leader_notes row if it looks like
    a real self-description (_ROLE_STATEMENT_RE), overwriting any prior note
    -- called once per incoming team-chat message, before routing, so it
    fires regardless of which reply path (lookup/data_chat/general chat)
    ends up handling the message."""
    text = (text or "").strip()
    if not text or not _ROLE_STATEMENT_RE.search(text):
        return
    note = text[:_LEADER_NOTE_MAX_CHARS]
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO leader_notes (asker_name, note, updated_at) VALUES (?, ?, datetime('now'))
               ON CONFLICT(asker_name) DO UPDATE SET note = excluded.note, updated_at = excluded.updated_at""",
            (asker_name, note),
        )
    log.info("data_chat: learned leader note for %s: %r", asker_name, note)


def _matching_candidates(reply_text: str, rows: list[dict], name_key: str) -> list[dict]:
    """Rows among `rows` whose name contains every word of `reply_text` --
    matches a bare "Crook" or "Bill Crook" follow-up against "Bill Crook",
    but not a full new question that happens to mention the same word."""
    reply_words = set(_NAME_WORD_RE.findall(reply_text.lower()))
    if not reply_words:
        return []
    return [r for r in rows if reply_words.issubset(_NAME_WORD_RE.findall(str(r.get(name_key, "")).lower()))]


def _try_resolve_pending_clarification(asker_name: str, question: str) -> tuple[bool, str | None] | None:
    """Returns the (on_topic, reply) to send back if `question` looks like
    the asker's answer to a clarifying question Watson just asked them;
    None if there's no pending clarification to resolve, so the caller
    should run the normal pipeline instead."""
    pending = _pending_clarifications.get(asker_name)
    if not pending:
        return None
    if time.monotonic() - pending["asked_at"] > _PENDING_CLARIFICATION_TTL_SECONDS:
        _forget_pending_clarification(asker_name)
        return None
    if pending.get("kind") == "event":
        if _EVENT_PICK_CANCEL_RE.match(question or ""):
            _forget_pending_clarification(asker_name)
            return True, "Okay, never mind."
        picks = _event_pick(question, pending["titles"])
        if len(picks) == 1:
            _forget_pending_clarification(asker_name)
            rewritten = _question_with_event(pending["question"], pending["phrase"], pending["titles"][picks[0]])
            log.info("data_chat: event choice resolved, asker=%s reply=%r -> q=%r", asker_name, question, rewritten)
            return answer_data_question(rewritten, asker_name)
        if len(picks) > 1:
            names = " or ".join(pending["titles"][i] for i in picks)
            _pending_clarifications[asker_name]["asked_at"] = time.monotonic()
            return True, f"Still more than one: {names}. Which one did you mean?"
        _forget_pending_clarification(asker_name)       # not an answer: a new question, run the normal pipeline
        return None
    matches = _matching_candidates(question, pending["rows"], pending["name_key"])
    if len(matches) == 1:
        _forget_pending_clarification(asker_name)
        return True, _format_rows(matches)
    if len(matches) > 1:
        # Narrowed but still ambiguous (e.g. "Bill" still matches all of
        # them) -- keep waiting, ask again with just the narrowed set.
        _remember_pending_clarification(asker_name, matches, pending["name_key"])
        names_list = ", ".join(str(r.get(pending["name_key"])) for r in matches[:15])
        return True, f"Still more than one: {names_list}. Which one did you mean?"
    # No candidate matched at all -- not an answer to the pending question,
    # so drop it and let the normal pipeline treat this as a new question.
    _forget_pending_clarification(asker_name)
    return None


def _clarify_if_ambiguous_person(sql: str, rows: list[dict], question: str, asker_name: str) -> str | None:
    """If `sql` looked someone up by members.name (not a group/event filter
    like `deacon LIKE` or `event_name LIKE`, which are supposed to return
    several different people) and the match pulled in more than one distinct
    person, return a clarifying question instead of a reply the caller
    should format normally (None).

    Per Bill's 2026-09-11 feedback: Tyler asked a question and Watson sent
    back multiple phone numbers with no attempt to narrow it down first --
    it should ask which person was meant, and only fall back to listing
    every match if the asker still can't specify further. If the asker's
    question already names each matched person individually (a deliberate
    "give me both" follow-up), that's not ambiguity -- let it through.
    """
    if len(rows) < 2 or not _NAME_COLUMN_LOOKUP_RE.search(sql):
        return None
    name_key = next((k for k in rows[0] if k.lower() == "name"), None)
    if not name_key:
        return None
    distinct_names = list(dict.fromkeys(r.get(name_key) for r in rows if r.get(name_key)))
    if len(distinct_names) < 2:
        return None
    q_lower = question.lower()
    if all(str(n).lower() in q_lower for n in distinct_names):
        return None
    _remember_pending_clarification(asker_name, rows, name_key)
    names_list = ", ".join(str(n) for n in distinct_names[:15])
    return (
        f"A few people match that: {names_list}. Which one did you mean? "
        "(Or ask again naming each one if you want more than one.)"
    )


_DOMAIN_RE = re.compile(r"DOMAIN:\s*(attendance|web|events|none)", re.IGNORECASE)
_SQL_RE = re.compile(r"SQL:\s*(.+)", re.IGNORECASE | re.DOTALL)
_FORBIDDEN_SQL_RE = re.compile(
    r";|--|/\*|\b(insert|update|delete|drop|alter|attach|detach|pragma|create|replace|vacuum|reindex)\b",
    re.IGNORECASE,
)
_TABLE_RE = re.compile(r"\bfrom\s+([a-zA-Z_]\w*)|\bjoin\s+([a-zA-Z_]\w*)", re.IGNORECASE)


def _generate(question: str, asker_name: str, allow_contact_info: bool) -> tuple[str | None, str | None]:
    """Returns (domain, sql). domain is None if the call failed or the
    model's output couldn't be parsed at all; "none" if it parsed fine but
    the question isn't attendance/web-traffic."""
    system = _SYSTEM_TEMPLATE.format(
        today=date.today().isoformat(),
        attendance_schema=_attendance_schema(allow_contact_info),
        web_schema=_WEB_SCHEMA,
        events_schema=_EVENTS_SCHEMA,
        contact_example=_CONTACT_ALLOWED_EXAMPLE if allow_contact_info else _CONTACT_BLOCKED_EXAMPLE,
    )
    resolved_question = _resolve_first_person(question, asker_name)
    # Prior turns from this leader's rolling conversation buffer (see
    # get_conversation_turns above) -- lets a follow-up like "what about last
    # week" or a question that only makes sense after something the leader
    # said earlier (e.g. Kaci mentioning she handles event registrations)
    # resolve correctly instead of every call starting from nothing. The
    # strict DOMAIN:/SQL: output contract in _SYSTEM_TEMPLATE still governs
    # the reply shape regardless of what's in this history.
    history = get_conversation_turns(asker_name)

    claude_result = call_claude(
        system=system, user=resolved_question, job_name="analytics.data_chat",
        person=asker_name, message=question, history=history,
    )
    if claude_result:
        content = claude_result
    else:
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={
                    "model": MODEL,
                    "messages": [{"role": "system", "content": system}]
                    + history
                    + [{"role": "user", "content": resolved_question}],
                    "stream": False,
                    "options": {"temperature": 0},
                },
                timeout=90,
            )
            resp.raise_for_status()
            content = resp.json()["message"]["content"].strip()
        except Exception as exc:
            log.error("data_chat: generation call failed: %s", exc)
            return None, None

    dmatch = _DOMAIN_RE.search(content)
    if not dmatch:
        log.warning("data_chat: no DOMAIN line in model output: %r", content)
        return None, None
    domain = dmatch.group(1).lower()
    if domain == "none":
        return "none", None

    smatch = _SQL_RE.search(content)
    if not smatch:
        log.warning("data_chat: DOMAIN=%s but no SQL line in: %r", domain, content)
        return domain, None

    sql = smatch.group(1).strip().strip("`")
    sql = re.sub(r"^sql\s*\n", "", sql, flags=re.IGNORECASE)
    sql = " ".join(sql.split())  # flatten to one line even if the model wrapped anyway
    return domain, sql.rstrip(";")


def _validate_sql(domain: str, sql: str | None, allow_contact_info: bool) -> str | None:
    """Returns sql unchanged if it's a single safe SELECT scoped to
    `domain`'s whitelisted tables/columns, else None."""
    if not sql or not sql.lower().lstrip().startswith("select"):
        return None
    if _FORBIDDEN_SQL_RE.search(sql):
        log.warning("data_chat: rejected SQL (forbidden token), domain=%s: %s", domain, sql)
        return None
    tables = {(m.group(1) or m.group(2)).lower() for m in _TABLE_RE.finditer(sql)}
    if not tables or not tables.issubset(_ALLOWED_TABLES[domain] | _ALWAYS_ALLOWED_TABLES):
        log.warning("data_chat: rejected SQL (tables %s not subset of %s): %s", tables, _ALLOWED_TABLES[domain], sql)
        return None
    if "members" in tables:
        blocked = _ALWAYS_BLOCKED_COLUMN_WORDS if allow_contact_info else _ALWAYS_BLOCKED_COLUMN_WORDS | _CONTACT_COLUMN_WORDS
        lowered = sql.lower()
        for word in blocked:
            if re.search(rf"\b{word}\b", lowered):
                log.warning("data_chat: rejected SQL (blocked column %r): %s", word, sql)
                return None
    return sql


def _run(domain: str, sql: str) -> list[dict] | None:
    path = _DB_PATH[domain]
    exec_sql = sql if re.search(r"\blimit\b", sql, re.IGNORECASE) else sql + " LIMIT 50"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(exec_sql)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        log.error("data_chat: query failed (%s): %s", exc, exec_sql)
        return None


def _fmt_value(v):
    if isinstance(v, float):
        v = round(v, 1)
    return "N/A" if v is None else v


def _events_not_tracked_reply() -> str:
    """Bill Crook asked about the 75th anniversary celebration on
    2026-09-17 (telegram_log ids 477-480) and got the generic "didn't turn
    up any matching data" -- indistinguishable from a real query bug. An
    empty-result EVENTS query means either the named event isn't in
    church_events at all (a LIKE '%...%' that matches nothing), OR it is
    tracked but has zero registrations so far -- the two are
    indistinguishable from rows alone, and asserting "I'm not tracking
    that" is an outright false claim in the second case (caught 2026-09-18
    asking about a just-created event with no signups yet). So this stays
    deliberately non-committal about which case it is and just lists what
    IS tracked, so the asker can tell for themselves rather than being
    told something that might be wrong."""
    try:
        conn = sqlite3.connect(f"file:{WATSON_DB_PATH}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT event_name, start_date, event_time FROM church_events "
            "WHERE tracking_active = 1 ORDER BY start_date"
        ).fetchall()
        conn.close()
    except Exception:
        rows = []
    if not rows:
        return "I don't have any events set up right now, so there's no registration data to check."
    tracked = ", ".join(_fmt_tracked_event(r) for r in rows)
    return f"I didn't find any matching registrations. If you meant one of these, let me know: {tracked}."


def _fmt_tracked_event(row: dict) -> str:
    """One tracked event as "name (date, time)" -- omits the parenthetical
    (or just the missing half) when start_date/event_time weren't set yet,
    which the Telegram new-event-notice path (bot.py) allows."""
    when = ", ".join(p for p in (row["start_date"] or None, row["event_time"] or None) if p)
    return f"{row['event_name']} ({when})" if when else row["event_name"]


def _format_rows(rows: list[dict], domain: str | None = None) -> str:
    if not rows:
        if domain == "events":
            return _events_not_tracked_reply()
        return "That didn't turn up any matching data."
    # LAST ATTENDED / LAST MISSED BY NAME (cdb_query.py's _pattern_match)
    # name these exact column shapes so this generic formatter can route them
    # through the same weeks-ago + campus sentence bot.py's DM path uses,
    # instead of the generic "col: val" dump below -- added 2026-09-15.
    if len(rows) == 1 and set(rows[0].keys()) == {"name", "last_attended", "campus"}:
        from jobs.analytics.attendance_reply import format_last_attended_reply
        r = rows[0]
        return format_last_attended_reply(r["name"], r.get("last_attended"), r.get("campus"))
    # Kid version of the above (cdb_query.py's _resolve_kid_id-routed LAST
    # ATTENDED BY NAME branch, added 2026-10-01) -- class_name instead of
    # campus, since kids_checkin doesn't capture campus per check-in.
    if len(rows) == 1 and set(rows[0].keys()) == {"name", "last_attended", "class_name"}:
        from jobs.analytics.attendance_reply import format_last_attended_reply
        r = rows[0]
        return format_last_attended_reply(r["name"], r.get("last_attended"), class_name=r.get("class_name"))
    if len(rows) == 1 and set(rows[0].keys()) == {"name", "last_missed"}:
        from jobs.analytics.attendance_reply import format_last_missed_reply
        r = rows[0]
        return format_last_missed_reply(r["name"], r.get("last_missed"))
    # COMBINED + CUMULATIVE ATTENDANCE with campus breakdown (cdb_query.py's
    # _pattern_match "attendance for the last N weeks/months" block, unfiltered
    # to show Online/Wilmington/Hybrid/Kids) -- multiple rows, one per
    # campus/group. Added 2026-09-30 per Bill's request to include kids in
    # multi-week queries; kids_covered_days/kids_total_days (NULL on the
    # adult rows) added same day so the formatter can flag a period where
    # Kids unique_individuals is a partial/headcount-only figure.
    if rows and all(set(r.keys()) == {"span_label", "campus", "combined_total", "unique_individuals", "kids_covered_days", "kids_total_days"} for r in rows):
        from jobs.analytics.attendance_reply import format_period_attendance_breakdown
        return format_period_attendance_breakdown(rows)
    # COMBINED + CUMULATIVE ATTENDANCE single-campus (cdb_query.py's _pattern_match
    # "attendance for the last N weeks/months" block with campus filter) -- one row.
    if len(rows) == 1 and set(rows[0].keys()) == {"span_label", "campus", "combined_total", "unique_individuals"}:
        from jobs.analytics.attendance_reply import format_period_attendance_reply
        r = rows[0]
        return format_period_attendance_reply(
            r["span_label"], r["combined_total"], r["unique_individuals"], r.get("campus")
        )
    # PLAIN SINGLE-SUNDAY HEADCOUNT (cdb_query.py's _pattern_match unfiltered
    # HOW MANY ATTENDED block) -- one row per campus plus a 'Kids' row, same
    # routing convention as the two blocks above. Added 2026-09-30 per Bill's
    # request: a "this week"/smaller-scope attendance question should get the
    # same kind of plain-English total sentence the month/year span already
    # gets, not a raw row dump.
    if rows and all(set(r.keys()) == {"service_date", "campus", "total"} for r in rows):
        from jobs.analytics.attendance_reply import format_weekly_attendance_reply
        return format_weekly_attendance_reply(rows)
    # CLASSROOM ROSTER (cdb_query.py's "which kids were in a given class"
    # block): rows of (role, name) -> "Adults: a, b" / "Kids: c, d". 2026-10-07.
    if rows and all(set(r.keys()) == {"role", "name"} for r in rows):
        _adults = [str(r["name"]).strip() for r in rows if r["role"] == "Adults"]
        _kids = [str(r["name"]).strip() for r in rows if r["role"] == "Kids"]
        return (f"Adults: {', '.join(_adults) if _adults else 'none recorded'}\n"
                f"Kids: {', '.join(_kids) if _kids else 'none recorded'}")
    if len(rows) == 1 and len(rows[0]) == 1:
        return str(_fmt_value(next(iter(rows[0].values()))))
    if len(rows) == 1:
        return ", ".join(f"{k}: {_fmt_value(v)}" for k, v in rows[0].items())
    # A list-style answer (multiple rows) where every row is a single column
    # reads far more naturally as one bare value per line than a repeated
    # "column_name: value" — the column name is self-evident once for a
    # whole list, and the system prompt already steers the model to
    # concatenate multi-field "list of people" answers (name, count, etc.)
    # into one such column rather than returning them split out raw.
    if all(len(r) == 1 for r in rows):
        lines = [str(_fmt_value(next(iter(r.values())))) for r in rows[:25]]
    elif all({"name", "email", "phone"}.issubset(r.keys()) for r in rows):
        # A list of PEOPLE with contact info — Bill's 2026-09-27 "format
        # lists in telegram" cleanup: 3 lines per person (name, phone,
        # email) instead of one cluttered "name: X, email: Y, phone: Z, ..."
        # line. Matches the format jobs/analytics/conversion_report.py's
        # fast paths use for the same shape of answer.
        lines = []
        for r in rows[:25]:
            lines.append(str(_fmt_value(r["name"])))
            lines.append(str(_fmt_value(r["phone"])) if r.get("phone") not in (None, "") else "N/A")
            lines.append(str(_fmt_value(r["email"])) if r.get("email") not in (None, "") else "N/A")
            lines.append("")
        if lines and lines[-1] == "":
            lines.pop()
    else:
        lines = [", ".join(f"{k}: {_fmt_value(v)}" for k, v in r.items()) for r in rows[:25]]
    if len(rows) > 25:
        lines.append(f"... and {len(rows) - 25} more rows.")
    return "\n".join(lines)


def _try_conversion_report(question: str) -> str | None:
    """LLM-free fast path for "guest conversion report" / "retention
    report" phrasings — see jobs/analytics/conversion_report.py. Unlike
    the SQL-generating fast paths below, this one computes and formats the
    answer itself (a rolling-window regular-status check isn't expressible
    as one SELECT), so it's called directly rather than through
    _validate_sql/_run. Added 2026-09-27 per Bill's request."""
    try:
        from jobs.analytics.conversion_report import try_conversion_report
    except Exception:
        return None
    return try_conversion_report(question, CONGREGATION_DB_PATH)


def _try_first_time_guest_count(question: str) -> str | None:
    """LLM-free fast path for "how many first-time guests..." — separate
    from _try_conversion_report because that trigger requires the phrase
    "conversion report"/"retention report", which a bare "how many"
    question doesn't contain. Added 2026-09-27 after Bill's Telegram log
    showed two such questions each wrongly answered with a full list of
    name/email/phone rows instead of a count (no fast path recognized
    them, so they fell to the LLM-generated-SQL path, which isn't reliably
    distinguishing a "how many" count shape from a "who" list shape)."""
    try:
        from jobs.analytics.conversion_report import try_first_time_guest_count
    except Exception:
        return None
    return try_first_time_guest_count(question, CONGREGATION_DB_PATH)


def _try_first_time_guest_list(question: str, allow_contact_info: bool) -> str | None:
    """LLM-free fast path for "who are/who were the first-time guests..." --
    Bill's explicit counterpart rule to _try_first_time_guest_count
    (2026-09-27): this question shape should always get names and contact
    info, never just a number. allow_contact_info is threaded through same
    as the rest of this module so a future narrower caller (bot.py
    currently always passes True) still gets contact info gated correctly."""
    try:
        from jobs.analytics.conversion_report import try_first_time_guest_list
    except Exception:
        return None
    return try_first_time_guest_list(question, CONGREGATION_DB_PATH, allow_contact_info)


def _try_pattern_match(question: str) -> str | None:
    """Bill's own cdb_query.py pattern-match layer, reused as a free
    LLM-free fast path for the common attendance phrasings it already
    recognizes. Its output still goes through _validate_sql() in the
    caller before being trusted."""
    try:
        from jobs.skills.cdb_query import _pattern_match, _last_sunday
    except Exception:
        return None
    weeks = [(date.today() - timedelta(weeks=i)).strftime("%Y-%m-%d") for i in range(1, 13)]
    return _pattern_match(question, _last_sunday(), weeks)


_SIGNUP_RE = re.compile(r"\b(signed[- ]?up|sign[- ]?ups?|registered|registrations?|rsvp'?d?|rsvps)\b", re.I)
# Only questions and requests get the guard's answers: "Hi Watson, this is Kaci. I handle event registrations" is not asking for numbers.
_QUESTION_RE = re.compile(r"\?|^\s*(?:hey|hi|hello)?\W*(?:watson\W*)?(how|who|who's|whos|what|which|is|are|can|could|does|do|did|where|when|list|show|give|tell|count)\b", re.I)
# "how many/who is coming/going to X": ordinary attendance wording too, so it only counts when X is a known event (see below).
_COMING_RE = re.compile(r"\b(how many|who(?:'s|\s+is|\s+are)?)\b.*\b(coming|going|attending|showing up)\b", re.I)
# Signup words that are NOT about a church event (serving, classes, groups): leave those to the normal routes -- but only when the question
# names no known event ("Servant Leaders Banquet" contains 'serv...' and is an event).
_SIGNUP_NOT_EVENT_RE = re.compile(r"\b(serv\w*|volunteer\w*|usher\w*|greet\w*|nursery|class\w*|room|kids?|group|team|rotation|schedule)\b", re.I)


def _tracked_event_names() -> list[str]:
    try:
        with sqlite3.connect(f"file:{WATSON_DB_PATH}?mode=ro", uri=True, timeout=5) as c:
            return [r[0] for r in c.execute("SELECT event_name FROM church_events WHERE tracking_active = 1 ORDER BY event_name")]
    except Exception:
        return []


_BILL_NAMES = {"bill", "bill yomes", "dr. bill yomes", "dr bill yomes", "dr. bill", "dr bill", "william yomes"}


def _stored_but_paused(title: str) -> bool:
    """True when Subsplash registration reading is paused (Bill's switch, waiting on Subsplash's answer) AND a copy of this event's signups is
    stored. The copy exists but chat must not use it, so the honest reply is "paused", not "I don't have numbers"."""
    try:
        with sqlite3.connect(f"file:{WATSON_DB_PATH}?mode=ro", uri=True, timeout=5) as c:
            sw = c.execute("SELECT value FROM system_settings WHERE key = ?", ("subsplash_registrations_paused",)).fetchone()
            if not (sw and sw[0] == "1"):
                return False
            return c.execute("SELECT 1 FROM subsplash_event_regs WHERE lower(title) = lower(?) AND has_form = 1 LIMIT 1", (title,)).fetchone() is not None
    except Exception:
        return False


def _untracked_signup_reply(question: str, asker_name: str | None = None) -> str | None:
    """A signup question the events fast path could not answer. Three honest outcomes instead of letting the model guess a query
    against some other table (2026-10-06: it picked group_attendance.num_tickets and errored):
      * the question could mean 2+ known events  -> ask which ("Men's Fraternity" = Bible Study or Billiards Outing);
      * it names exactly one event Watson does not track -> say so by name;
      * it names no known event -> the generic "no signup numbers for that event" (unless it is really about serving/classes/groups)."""
    if not _QUESTION_RE.search(question):
        return None                                   # a statement ("I handle event registrations", "I created an event ... to track registrations")
    is_signup = bool(_SIGNUP_RE.search(question))
    is_coming = bool(_COMING_RE.search(question))
    if not (is_signup or is_coming):
        return None
    try:
        from jobs.events.pattern_match import event_candidates
        info = event_candidates(question)
    except Exception:
        info = {"phrase": "", "candidates": []}
    cands = info["candidates"]
    if is_coming and not is_signup and not cands:
        return None                                   # "how many are coming to church" etc.: attendance wording, not our business
    if not cands and _SIGNUP_NOT_EVENT_RE.search(question):
        return None
    names = _tracked_event_names()
    have = [c["title"] for c in cands if c["tracked"]]
    if len(cands) > 1:
        titles = " or ".join(c["title"] for c in cands)
        tail = (f" I have signup numbers for {', '.join(have)}." if have else " I don't have signup numbers for any of those.")
        if asker_name:                                # a short answer ("Men's Frat Bible Study", "billiards", "the second one") completes this question
            _remember_pending_event_choice(asker_name, question, info["phrase"], [c["title"] for c in cands])
        return f"Which one do you mean: {titles}?{tail}"
    if len(cands) == 1:
        if cands[0]["tracked"]:
            return None                               # tracked, but the fast path declined (e.g. asks about a custom form field): let the model try
        title = cands[0]["title"]
        tail = (" I do track signups for: " + ", ".join(names) + ".") if names else ""
        if _stored_but_paused(title):
            if (asker_name or "").strip().lower() in _BILL_NAMES:
                return (f"I have a copy of the Subsplash signups for {title}, but I'm not using it while Subsplash registration reading is paused "
                        f"(until Subsplash answers). I'll use it as soon as the pause is lifted." + tail)
            return f"I can't give you signup numbers for {title} right now." + tail + " For anything else, ask Dr. Bill."
        return f"I don't have signup numbers for {title}." + tail
    tail = (" I do track signups for: " + ", ".join(names) + ".") if names else ""
    return "I don't have signup numbers for that event." + tail + " For anything else, ask Dr. Bill."


def _try_pattern_match_events(question: str) -> str | None:
    """LLM-free fast path for common event-signup phrasings (RSVP counts/
    lists, "what events are we tracking") — see jobs/events/pattern_match.py.
    Mirrors _try_pattern_match's reuse pattern above but for the events
    domain. Added 2026-09-14 after two picnic questions each triggered a
    real Claude API call (core/claude_tier.py, ~$0.011 each) because no
    events-domain fast path existed yet — only attendance had one."""
    try:
        from jobs.events.pattern_match import pattern_match
    except Exception:
        return None
    return pattern_match(question)


def answer_data_question(
    question: str, asker_name: str, allow_contact_info: bool = True
) -> tuple[bool, str | None]:
    """Try to answer an attendance/web-traffic (and, per Bill's 2026-09-02
    decision that all Catalyst leaders get full access with no distinction
    between staff/elders/deacons, contact-info) question with a real query.

    Returns (on_topic, reply). on_topic=False means the question wasn't one
    of those at all -- the caller decides what to do with an off-topic
    question. on_topic=True always comes with a reply (an answer, or an
    apologetic failure message) and should be sent back as-is.
    """
    # Checked before anything else so a short "which one did you mean"
    # follow-up (e.g. "Crook") resolves against the candidates a prior
    # clarifying question offered, rather than being run through SQL
    # generation, which has no verb/question shape to work with for a bare
    # name reply -- see _try_resolve_pending_clarification's docstring.
    resolved = _try_resolve_pending_clarification(asker_name, question)
    if resolved is not None:
        return resolved

    # Guest conversion/retention report -- checked before the generic
    # attendance pattern-match below since "conversion report"/"retention
    # report" is specific phrasing that won't collide with it, and this
    # handler computes its own answer directly rather than producing SQL
    # for _validate_sql/_run (see _try_conversion_report's docstring).
    conversion_reply = _try_conversion_report(question)
    if conversion_reply is not None:
        log.info("data_chat: conversion-report hit, asker=%s q=%r", asker_name, question)
        return True, conversion_reply

    # "How many first-time guests..." -- checked next, before the count
    # question can reach the LLM-generated-SQL path below (see
    # _try_first_time_guest_count's docstring for the bug this fixes).
    count_reply = _try_first_time_guest_count(question)
    if count_reply is not None:
        log.info("data_chat: first-time-guest-count hit, asker=%s q=%r", asker_name, question)
        return True, count_reply

    # "Who are/who were the first-time guests..." -- the explicit mirror
    # image Bill asked for right after the count fix above.
    who_reply = _try_first_time_guest_list(question, allow_contact_info)
    if who_reply is not None:
        log.info("data_chat: first-time-guest-list hit, asker=%s q=%r", asker_name, question)
        return True, who_reply

    # "Who registered but didn't come to Men's Fraternity?" -- registered (Subsplash) vs attended (group_attendance), LLM-free.
    # Before the generic pattern-match and the signup guard below, which would otherwise treat the sign-up wording as an event question.
    try:
        from jobs.analytics.group_compare import answer as _group_compare_answer
        group_reply = _group_compare_answer(question)
    except Exception:
        log.exception("data_chat: group registered-vs-attended failed, falling through")
        group_reply = None
    if group_reply is not None:
        log.info("data_chat: group registered-vs-attended hit, asker=%s q=%r", asker_name, question)
        return True, group_reply

    # "Who is serving this coming Sunday / this week?" -- volunteer schedule (Fluro, scheduling-only), LLM-free. Needs a serving word
    # AND a Sunday/week word, so it sits before the generic pattern-matches that read "who is ..." as a person lookup.
    try:
        from jobs.analytics.serving_schedule import answer as _serving_answer
        serving_reply = _serving_answer(question)
    except Exception:
        log.exception("data_chat: serving schedule failed, falling through")
        serving_reply = None
    if serving_reply is not None:
        log.info("data_chat: serving-schedule hit, asker=%s q=%r", asker_name, question)
        return True, serving_reply

    # Found 2026-09-02 debugging Donna's "who is in Bill Crook's deacon
    # group?" -- cdb_query._pattern_match()'s 'who is'/'tell me about'
    # trigger is built for simple name lookups and can misfire on a longer
    # question, treating the WHOLE question as a person's name. That query
    # still runs cleanly and (correctly) finds nobody named "in bill
    # crook's deacon group", so a bare "rows is not None" check was trusting
    # a false-positive match as a genuine empty-result answer and never
    # gave the smarter model-generated query below a chance to try. Only
    # short-circuit on the fast path when it actually found something --
    # zero rows (or an execution error) falls through to generation instead.
    pm_sql = _validate_sql("attendance", _try_pattern_match(question), allow_contact_info)
    if pm_sql:
        rows = _run("attendance", pm_sql)
        if rows:
            log.info("data_chat: pattern-match hit, asker=%s q=%r sql=%r rows=%d", asker_name, question, pm_sql, len(rows))
            clarify = _clarify_if_ambiguous_person(pm_sql, rows, question, asker_name)
            return True, clarify or _format_rows(rows)
        log.info("data_chat: pattern-match matched but found nothing (rows=%s), falling through to generation: q=%r sql=%r", rows, question, pm_sql)

    # Same free/LLM-free short-circuit as the attendance block above, for
    # the events domain — see _try_pattern_match_events's docstring.
    pm_events_sql = _validate_sql("events", _try_pattern_match_events(question), allow_contact_info)
    if pm_events_sql:
        rows = _run("events", pm_events_sql)
        if rows:
            log.info("data_chat: events pattern-match hit, asker=%s q=%r sql=%r rows=%d", asker_name, question, pm_events_sql, len(rows))
            return True, _format_rows(rows)
        # A tracked event with nobody signed up yet: say that, don't hand a plain "who is signed up" to the model (it cost a call and
        # could guess a table). Count queries never get here (COALESCE gives a 0 row).
        m = re.search(r"event_registrations WHERE event_id = (\d+)", pm_events_sql)
        if rows == [] and m:
            try:
                with sqlite3.connect(f"file:{WATSON_DB_PATH}?mode=ro", uri=True, timeout=5) as c:
                    nm = c.execute("SELECT event_name FROM church_events WHERE id = ?", (int(m.group(1)),)).fetchone()
            except Exception:
                nm = None
            if nm:
                log.info("data_chat: events pattern-match hit but nobody signed up yet, asker=%s q=%r", asker_name, question)
                return True, f"Nobody has signed up for {nm[0]} yet."
        log.info("data_chat: events pattern-match matched but found nothing (rows=%s), falling through to generation: q=%r sql=%r", rows, question, pm_events_sql)

    # Signup question the events fast path could not match -> an event we do not track. Answer honestly, no model guess.
    untracked = _untracked_signup_reply(question, asker_name)
    if untracked:
        log.info("data_chat: untracked-event signup question, asker=%s q=%r", asker_name, question)
        return True, untracked

    domain, sql = _generate(question, asker_name, allow_contact_info)
    if domain in (None, "none"):
        return False, None

    validated = _validate_sql(domain, sql, allow_contact_info)
    if not validated:
        return True, "I couldn't work out a safe way to answer that from the data I have — try rephrasing, or ask Dr. Bill."

    rows = _run(domain, validated)
    if rows is None:
        return True, "I hit an error pulling that data — try again in a moment."

    log.info("data_chat: domain=%s asker=%s q=%r sql=%r rows=%d", domain, asker_name, question, validated, len(rows))
    clarify = _clarify_if_ambiguous_person(validated, rows, question, asker_name)
    return True, clarify or _format_rows(rows, domain)
