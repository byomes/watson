# Small group attendance + connectedness: sketch (2026-10-06, not built)

Goal: record who came to Men's Fraternity, Celebrate Recovery, Woven, Remix, Men's Breakfast,
etc., so team chat can answer "who was at Men's Fraternity last week?" and trends.

## Data
New table in congregation.db (same file as `attendance`, so team chat can join to members):
  group_attendance(id, series TEXT, event_date TEXT, member_id INTEGER NULL,
                   guest_name TEXT NULL, status TEXT DEFAULT 'present',
                   recorded_by TEXT, source TEXT, created_at)
  UNIQUE(series, event_date, member_id)
`series` = church_calendar_events.series ("Small Groups|Men's Fraternity Bible Study"), so every
record ties to the calendar. Guests (no member row) keep a name only. Optional per-session
head count (group_headcount: series, event_date, count) for groups that do not take names.

## Ways to record (smallest first)
1. Telegram, from the group's leader: "Men's Fraternity tonight: Bill Crook, Jim Bouchat, 2 guests".
   Parsed with the existing member name matching + "which Bill?" disambiguation. Watson
   replies with who it recorded and who it could not match, for the leader to confirm.
2. Roster page (wtsn.me, leader PIN): tick names from the series' roster; prefilled with
   Subsplash registrants and last session's attendees. Best for phones in the room.
3. Follow-up nudge: after a session ends (from the calendar end time), ask only that group's
   leader "Who was there?" next morning (9am+, never after 8pm). Needs your approval to schedule.

## Who records
Group leaders from leadership_roles (e.g. Men's Fraternity leader = Gerry DiMatteo,
Celebrate Recovery = Bill Williamson/Deborah Noel, Remix = Tyler McCauley, Woven = Letha
Palmer). Many are not onboarded in Telegram yet, so option 2 or the deacon-meeting
onboarding comes first.

## Reading it back
Add group_attendance to data_chat.py's attendance whitelist + schema text, so every
onboarded team member can ask it, same access as Sunday attendance. Add a couple of
cdb-style fast paths ("who was at X last week", "how many came to X").

## Decisions for Bill
- Names or head counts only, per group? (Celebrate Recovery is confidential; recommend
  head count only there, no names, and keep it out of team chat.)
- Recording method: Telegram, roster page, or both.
- Do group leaders get to see other groups' lists?

## Bill's decisions (2026-10-06)
- Individual (named) attendance for EVERYTHING except Celebrate Recovery. CR: no individual
  tracking at all, head count only, and kept out of team chat and the connectedness score.
- Wants all attendance and serving tools combined into ONE app/page eventually; track it all.
- Purpose: measure connectedness. Small group + serving activity is the key signal.

## Unified "Connection" view (proposal)
One per-person record joining sources that already exist or are planned:
  worship   -> attendance (Sunday)                       [exists]
  groups    -> group_attendance (this sketch)            [new]
  serving   -> team_memberships + started_serving_date   [exists]; add serving_log (date, team, member) for actual shifts [new]
  events    -> event_registrations (+ banquet RSVPs)     [exists]
  kids      -> kids_checkin for parents                  [exists]
Connection score = simple, explainable counts over a trailing window (e.g. 90 days):
  worship weeks attended, group sessions attended, serving shifts, events. Show the four
  numbers, not one magic number; flag "worship-only" (attends, no group, no serving) and
  "dropping" (was active, now silent). Maps onto the 4-stage assimilation pathway
  (project_assimilation_pathway): the score is the evidence for a person's stage.
Surface: a CatalystDB-style page (wtsn.me/cat/...) behind the existing PIN, same audience as
team chat (elders, deacons, staff). Follow-up goes to shepherds as elder/deacon work, never Bill.

## Order of work
1. group_attendance table + recording (roster page first) for all series except CR.
2. serving_log: how do shifts get recorded today? (needs Bill's answer; Subsplash Teams?)
3. Per-person Connection page (read-only join of the above) + team chat "how connected is X".
4. Shepherd nudges for worship-only/dropping people (needs Bill's approval).
