# Subsplash access scope for Watson (2026-10-09)

Subsplash confirmed (via Bill) that Watson reading the church's own dashboard and core.subsplash.com data is within their usage terms. robots.txt Disallow no longer blocks this for core.subsplash.com (explicit permission). Dashboard must still be surfaced on the work phone screen (surface_tab rule).

## Already built (now live again)
- Kids check-in history: core.subsplash.com/check-in/v1/end-user-check-ins + events/v2 (kids_checkin_import, Mon 6am cron)
- Event registrations/guest lists: dashboard pages (registrations.py, every 3h)
- Fluro: OFF LIMITS (Bill, 2026-10-09). Its database also holds giving records; Watson is shepherding-only. No contacts, serving teams, giving or the Watson-SMS list sync. fluro_client.get_session_token raises.
- Public calendars (4 embeds, hourly)

## Candidate pulls for usage + tracking (verify each endpoint on first use, read-only)
| Data | Source | Use |
|---|---|---|
| Event registrations via forms/v1/responses | core.subsplash.com | replaces page reading (faster, exact) |
| Event attendance / group check-ins (non-kids) | check-in/v1 | small group + event attendance, connectedness |
| Giving frequency (flags only, NO amounts surfaced) | giving/donations | lapsed/new giver signal; Bill-only, never to team chat |
| Group membership + group events | groups/events | small group rosters for group_attendance |
| Messaging inbound (shepherding alerts) | messaging | pastoral triage, Bill-only |
| App/media usage analytics | dashboard | OUT OF SCOPE (media) |

## Rules carried over
No CR names; sms/giving never on team surfaces; Fluro never a bulk source; write-side to Fluro (Watson-SMS list) needs the add/remove call captured first.

## Blocker 2026-10-09
First live run of kids_checkin_import got HTTP 401 on events list: the phone's dashboard session is logged out/expired. Needs Bill to send the SMS login code (see memory).

## Done 2026-10-09
Kids check-in pull fixed and backfilled (88 Sundays); registrations read via API (emails, head counts); events auto-synced into church_events. Dropped: non-kids check-in attendance, Fluro everything.
