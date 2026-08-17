# Kit → Brevo Migration Audit (Phase 1)

*Generated 2026-08-17. Audit only — no code changed in this pass.*
*Reviewed and approved by Bill 2026-08-17 — see "Decisions from review" below.*

## Context already established before this audit

Outbound transactional **email delivery is already fully on Brevo** —
`jobs/email_job/brevo_send.py`, "all 27 migrated call sites, no exceptions"
(see WATSON_ARCHITECTURE.md, Watson Identity & Email). The Book Launch
Campaign system's Brevo dispatcher (`jobs/campaigns/brevo_dispatcher.py`,
`brevo_send.py`-based) and a read-only Brevo contacts/lists client
(`jobs/campaigns/brevo_contacts.py`, used by Comms Desk) are also already
live. What Kit still owns, exclusively, is: **subscriber tagging (segmentation)**,
**broadcast/newsletter drafting**, and **one-time TWJ launch-page signup capture on
Vercel**. That's a narrower migration than "replace an email platform" — it's
"replace a tagging/broadcast platform that a few write paths still call directly."

## Touchpoint table

| # | File | What it does | Read/Write | Proposed Brevo replacement |
|---|------|---------------|------------|------------------------------|
| 1 | `watson/jobs/arc/api.py` (`_kit_tag_subscriber`, `/api/arc/apply`) | Tags every full ARC signup with Kit tag ID `19285341` (hardcoded `_ARC_TAG_ID`) via v3 `POST /tags/{id}/subscribe` | Write | Brevo `PUT /v3/contacts/{email}` (or `POST /v3/contacts` with `updateEnabled: true`) setting a boolean/text attribute, e.g. `ARC_READER=true`, plus optional Brevo list membership |
| 2 | `watson/jobs/arc_interest/api.py` (`_kit_tag_subscriber`) | Tags ARC *waitlist* signups with the same `_ARC_TAG_ID` (imported from #1 — Bill's call: interest-only signups share the ARC tag) | Write | Same attribute as #1 — no separate distinction needed in Brevo either, per existing design intent |
| 3 | `watson/jobs/lead_magnet/api.py` (`_kit_tag_subscriber`, ×2 call sites) | Tags lead-magnet signups with a per-magnet `kit_tag_id` (from `lead_magnets` table) **and** a shared `KIT_COMPANION_TAG_ID` cross-book tag | Write | Per-magnet **Brevo list membership** (one list per magnet slug, contact added to each list they claim — see Decisions below) + shared `COMPANION_GUIDE_READER=true` attribute |
| 4 | `watson/jobs/writing_room/onboard.py` (`_kit_tag`, `kit_tag_on_activation`) | Get-or-create Kit tag `writing-room-partner`, subscribes partner on activation | Write | `WRITING_ROOM_PARTNER=true` attribute (or Brevo list membership — Writing Room is a discrete cohort, a list may fit better than an attribute) |
| 5 | `watson/jobs/givebutter/sync.py` (`sync_donor_to_kit`, `_get_or_create_kit_tag`, `_kit_tag_subscriber`) | Daily 6am cron — tags every synced donor with `"donor"` + a computed segment tag (get-or-create + subscribe, v3) | Write | `DONOR=true` + `DONOR_SEGMENT=<segment>` attributes |
| 6 | `watson/jobs/campaigns/kit_import.py` | One-time/on-demand full Kit subscriber list pull (v3, paginated) → `book_launch_contacts` (`segment='general'`, `source='kit_export'`). `--dry-run` defaults true | Read | Superseded outright by Phase 3's `kit_export.py` (fuller export: contacts + lists + tags, not just active subscribers into one table) |
| 7 | `watson/jobs/email_job/draft_email.py` (`create_kit_broadcast`) | Thu 7am cron — drafts a **Kit broadcast** (v3 `POST /broadcasts`, `public: false`) from queued articles; Bill edits/sends manually in Kit's UI | Write | **Deferred, not Phase 4 scope** — see "Decisions from review": rewire into Comms Desk's `book_launch_sends`/`brevo_dispatcher.py` pipeline once that session's schema is confirmed. Left untouched for now |
| 8 | `watson/bot/bot.py` (`_gb_create_kit_draft`, called from `handle_edit_thank_callback`) | Givebutter "Edit" thank-you path — v3 `POST /broadcasts`, creates a personal thank-you draft in Kit for one donor | Write | **Deferred, not Phase 4 scope** — same Comms Desk target as #7. Left untouched for now |
| 9 | `watson/bot/bot.py` (`_gb_add_kit_reminder`) | Not a Kit API call — inserts a Watson `reminders` row telling Bill to "edit and send thank-you email to X in Kit," paired with #8 | Write (Watson DB only) | **Deferred** along with #8 — reword once that flow is rewired into Comms Desk, not before |
| 10 | `watson/bot/bot.py` (`_gb_get_kit_subscriber_id`) | v3 `GET /subscribers?email_address=` — looks up a Kit subscriber ID by email | Read | **Confirmed dead by Bill — delete in Phase 4, do not migrate** |
| 11 | `watson/bot/bot.py` (`_gb_send_kit_email`) | Despite the name, already sends via **Brevo** (`_brevo_send_email` / `brevo_send.py`) for the "Send" (not "Edit") Givebutter thank-you path — misleading name, not a real Kit touchpoint | Write (already Brevo) | No functional change — optional rename to `_gb_send_thank_you_email` for clarity, out of scope for this migration unless you want it bundled in |
| 12 | `watson/jobs/dashboard/app.py` (`/api/kit/subscribe` route) | v3 tag-subscribe endpoint keyed by `tag` param (`fms`/`wcky` → `KIT_FMS_TAG_ID`/`KIT_WCKY_TAG_ID`) | Write | **Confirmed dead by Bill — delete in Phase 4, do not migrate.** Same for `_gb_get_kit_subscriber_id` (#10) and `config/settings.py`'s `KIT_API_KEY` (#14) |
| 13 | `wcky/src/app/api/thewrongjesus/signup/route.ts` | TWJ launch page email signup — calls Kit **v4 directly from the Vercel serverless function** (`X-Kit-Api-Key` header): creates subscriber, then tags with `KIT_TWJ_TAG_ID` | Write | This is the one site that violates the "forms feed Watson first" pattern the rest of the system already follows. Phase 4 target: point this route at a new Watson endpoint (e.g. `POST /api/twj/signup`) that applies a `TWJ_LAUNCH_SIGNUP=true` Brevo attribute server-side, same shared-secret pattern as Writing Room/ARC |
| 14 | `watson/config/settings.py` (`KIT_API_KEY`) | Declares a `KIT_API_KEY` constant | — | **Confirmed dead by Bill — delete in Phase 4** |

## Env vars found

| Var | Where | Status |
|-----|-------|--------|
| `KIT_API_KEY` | Watson `.env`, `config/settings.py`, `bot.py`, `jobs/dashboard/app.py`, `jobs/givebutter/sync.py` | Set (v3 reads: tag list, subscriber lookup) |
| `KIT_API_SECRET` | Watson `.env`, most write sites above | Set (v3 writes: tag create/subscribe, broadcast create) |
| `KIT_API_KEY_V4` | Watson `.env` (present, blank), `bot.py` comment only | Declared, **unused** in Watson — v4 is only actually used by wcky's TWJ route (its own separate `KIT_API_KEY`/`KIT_TWJ_TAG_ID` on Vercel, not this var) |
| `KIT_SENDER_EMAIL`, `KIT_SENDER_NAME` | Watson `.env`, `bot.py`, `givebutter/sync.py` | Set — used as `from_name`/`from_email` on Kit broadcast creation only |
| `KIT_COMPANION_TAG_ID` | Watson `.env`, `lead_magnet/api.py` | Set |
| `KIT_FMS_TAG_ID`, `KIT_WCKY_TAG_ID` | Watson `.env`, `jobs/dashboard/app.py` (`/api/kit/subscribe`) | Set, but feed the orphaned route (#12) |
| `KIT_TWJ_TAG_ID` | wcky Vercel env (not Watson `.env`) | Per WATSON_ARCHITECTURE.md, listed as "pending" for the TWJ launch page |
| `KIT_API_KEY` (wcky) | wcky Vercel env, separate from Watson's copy | Used by `route.ts` (#13) |

No hardcoded tag IDs found beyond `_ARC_TAG_ID = 19285341` (#1/#2). No literal `"arc-reader"` or `"arc-complete"` tag-name strings exist anywhere in code — only `"writing-room-partner"` (#4) and `"companion-guide-reader"` (referenced in a log message only, #3) are actual tag names in source; ARC uses the numeric ID directly with no name lookup.

## Kit tag → Brevo attribute mapping — APPROVED 2026-08-17

| Kit tag / mechanism | Approved Brevo target | Notes |
|---|---|---|
| `_ARC_TAG_ID` (19285341) — ARC reader + ARC interest | `ARC_READER` (boolean) attribute | Same attribute for both full and interest signups, matching current Kit behavior |
| `writing-room-partner` | `WRITING_ROOM_PARTNER` (boolean) attribute | |
| per-lead-magnet `kit_tag_id` | **Brevo list membership — one list per magnet slug** | Changed from the original text-attribute proposal: a text attribute can only hold one value, which loses history if a contact ever claims two magnets. Bill's call: use Brevo's native multi-value list-membership primitive instead of working around a single-value field. Contact gets added to each magnet's list they claim |
| `KIT_COMPANION_TAG_ID` (companion-guide-reader) | `COMPANION_GUIDE_READER` (boolean) attribute | |
| `"donor"` + segment tag (Givebutter) | `DONOR` (boolean) + `DONOR_SEGMENT` (text) attributes | |
| TWJ launch signup tag (`KIT_TWJ_TAG_ID`) | `TWJ_LAUNCH_SIGNUP` (boolean) attribute | New Watson-routed endpoint, see #13 |

## Decisions from review (2026-08-17)

- **Tag mapping approved** as above, with the lead-magnet change from text
  attribute → per-slug Brevo list membership (see table).
- **Broadcast drafting (#7, #8, #9) deferred, out of Phase 4 scope.** Do not
  build a standalone drafting/delivery mechanism for these. There is a
  parallel in-progress build (Comms Desk — draft/approve/schedule UI writing
  into `book_launch_sends` under a general-comms campaign, dispatched by the
  existing `brevo_dispatcher.py`) that is the correct target for both sites.
  `draft_email.py` and the Givebutter draft path in `bot.py` stay untouched
  through this migration's Phase 4. Follow-up task for later: rewire both
  into Comms Desk's pipeline once that session's table shape is confirmed —
  do not guess at or build against that shape speculatively from this side.
- **Orphaned code (#10, #12, #14) confirmed dead — delete in Phase 4,** don't
  port: `/api/kit/subscribe` route, `_gb_get_kit_subscriber_id`, and
  `config/settings.py`'s unused `KIT_API_KEY` constant.
- **wcky's direct Kit call (#13) confirmed in scope, proceeds as originally
  spec'd** — route `/api/thewrongjesus/signup` through Watson first.

## Out of scope / not found

- No Kit references in `watson-admin` beyond a false-positive (`FeedbackItem` interface name)
- No genuine Kit references in dashboard templates/CSS/JS — all "kit" hits were `-webkit-` CSS false positives
- `jobs/campaigns/brevo_dispatcher.py`, `brevo_send.py`, `brevo_contacts.py` are already Brevo-only, no Kit involvement — not migration targets, just noted as existing infrastructure Phase 4 can reuse

---

**Phase 1 approved 2026-08-17 — proceeding to Phase 2 (suppression list transfer, dry-run only).**
