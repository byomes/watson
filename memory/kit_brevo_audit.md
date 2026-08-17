# Kit → Brevo Migration Audit (Phase 1)

*Generated 2026-08-17. Audit only — no code changed in this pass.*

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
| 3 | `watson/jobs/lead_magnet/api.py` (`_kit_tag_subscriber`, ×2 call sites) | Tags lead-magnet signups with a per-magnet `kit_tag_id` (from `lead_magnets` table) **and** a shared `KIT_COMPANION_TAG_ID` cross-book tag | Write | Per-magnet attribute (e.g. `LEAD_MAGNET=<slug>`) + shared `COMPANION_GUIDE_READER=true` attribute |
| 4 | `watson/jobs/writing_room/onboard.py` (`_kit_tag`, `kit_tag_on_activation`) | Get-or-create Kit tag `writing-room-partner`, subscribes partner on activation | Write | `WRITING_ROOM_PARTNER=true` attribute (or Brevo list membership — Writing Room is a discrete cohort, a list may fit better than an attribute) |
| 5 | `watson/jobs/givebutter/sync.py` (`sync_donor_to_kit`, `_get_or_create_kit_tag`, `_kit_tag_subscriber`) | Daily 6am cron — tags every synced donor with `"donor"` + a computed segment tag (get-or-create + subscribe, v3) | Write | `DONOR=true` + `DONOR_SEGMENT=<segment>` attributes |
| 6 | `watson/jobs/campaigns/kit_import.py` | One-time/on-demand full Kit subscriber list pull (v3, paginated) → `book_launch_contacts` (`segment='general'`, `source='kit_export'`). `--dry-run` defaults true | Read | Superseded outright by Phase 3's `kit_export.py` (fuller export: contacts + lists + tags, not just active subscribers into one table) |
| 7 | `watson/jobs/email_job/draft_email.py` (`create_kit_broadcast`) | Thu 7am cron — drafts a **Kit broadcast** (v3 `POST /broadcasts`, `public: false`) from queued articles; Bill edits/sends manually in Kit's UI | Write | Brevo has no exact "broadcast draft" primitive the same shape — options: (a) draft an Email Campaign via `POST /v3/emailCampaigns` with `status` left unscheduled, or (b) draft into `book_launch_sends`-style Watson storage and let Bill approve/send through the existing campaign dispatcher. Needs a design decision, not just a swap — flagging for Phase 4 discussion, not building against yet |
| 8 | `watson/bot/bot.py` (`_gb_create_kit_draft`, called from `handle_edit_thank_callback`) | Givebutter "Edit" thank-you path — v3 `POST /broadcasts`, creates a personal thank-you draft in Kit for one donor | Write | Same open question as #7 (broadcast-draft equivalent) — a single-recipient "draft" is really just an unsent transactional email; simplest Brevo replacement is probably a Watson-side "pending send" row Bill approves, not a Kit-broadcast-shaped one-to-one port |
| 9 | `watson/bot/bot.py` (`_gb_add_kit_reminder`) | Not a Kit API call — inserts a Watson `reminders` row telling Bill to "edit and send thank-you email to X in Kit," paired with #8 | Write (Watson DB only) | Reword reminder text once #8's replacement flow is decided; no API change needed here itself |
| 10 | `watson/bot/bot.py` (`_gb_get_kit_subscriber_id`) | v3 `GET /subscribers?email_address=` — looks up a Kit subscriber ID by email | Read | **Appears unused** — grepped for callers in `bot.py`, none found. Confirm dead before writing a replacement; may just be removable |
| 11 | `watson/bot/bot.py` (`_gb_send_kit_email`) | Despite the name, already sends via **Brevo** (`_brevo_send_email` / `brevo_send.py`) for the "Send" (not "Edit") Givebutter thank-you path — misleading name, not a real Kit touchpoint | Write (already Brevo) | No functional change — optional rename to `_gb_send_thank_you_email` for clarity, out of scope for this migration unless you want it bundled in |
| 12 | `watson/jobs/dashboard/app.py` (`/api/kit/subscribe` route) | v3 tag-subscribe endpoint keyed by `tag` param (`fms`/`wcky` → `KIT_FMS_TAG_ID`/`KIT_WCKY_TAG_ID`) | Write | **Appears orphaned** — no caller found anywhere in `wcky` or `watson-admin` (grepped both repos for `api/kit/subscribe`, zero hits outside this file and the changelog entry that added it). Likely predates the ARC/TWJ "Watson-backed API" pattern. Recommend confirming with Bill it's dead, then removing rather than porting |
| 13 | `wcky/src/app/api/thewrongjesus/signup/route.ts` | TWJ launch page email signup — calls Kit **v4 directly from the Vercel serverless function** (`X-Kit-Api-Key` header): creates subscriber, then tags with `KIT_TWJ_TAG_ID` | Write | This is the one site that violates the "forms feed Watson first" pattern the rest of the system already follows. Phase 4 target: point this route at a new Watson endpoint (e.g. `POST /api/twj/signup`) that applies a `TWJ_LAUNCH_SIGNUP=true` Brevo attribute server-side, same shared-secret pattern as Writing Room/ARC |
| 14 | `watson/config/settings.py` (`KIT_API_KEY`) | Declares a `KIT_API_KEY` constant | — | **Dead** — grepped for importers, none found. No replacement needed, just noise |

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

## Proposed Kit tag → Brevo attribute mapping (for your approval before Phase 3 build)

| Kit tag / mechanism | Proposed Brevo contact attribute | Notes |
|---|---|---|
| `_ARC_TAG_ID` (19285341) — ARC reader + ARC interest | `ARC_READER` (boolean) | Same attribute for both full and interest signups, matching current Kit behavior |
| `writing-room-partner` | `WRITING_ROOM_PARTNER` (boolean) | Consider a Brevo **list** instead if you ever want to send a Brevo campaign to just this cohort — attributes can't be targeted as a send audience the way lists can |
| per-lead-magnet `kit_tag_id` | `LEAD_MAGNET` (text, slug value) | Kit's per-magnet tags become a single text attribute holding the slug, since a contact can only have one value — if a person could ever claim two magnets, this loses history a tag-per-magnet model wouldn't. Flagging that tradeoff rather than deciding it here |
| `KIT_COMPANION_TAG_ID` (companion-guide-reader) | `COMPANION_GUIDE_READER` (boolean) | |
| `"donor"` + segment tag (Givebutter) | `DONOR` (boolean) + `DONOR_SEGMENT` (text) | |
| TWJ launch signup tag (`KIT_TWJ_TAG_ID`) | `TWJ_LAUNCH_SIGNUP` (boolean) | New Watson-routed endpoint, see #13 |

**Open question, not decided in this pass:** #7/#8 (Kit broadcast drafting) have no clean 1:1 Brevo equivalent — needs a design call before Phase 4 touches them.

## Out of scope / not found

- No Kit references in `watson-admin` beyond a false-positive (`FeedbackItem` interface name)
- No genuine Kit references in dashboard templates/CSS/JS — all "kit" hits were `-webkit-` CSS false positives
- `jobs/campaigns/brevo_dispatcher.py`, `brevo_send.py`, `brevo_contacts.py` are already Brevo-only, no Kit involvement — not migration targets, just noted as existing infrastructure Phase 4 can reuse

---

**Stopping here per spec — awaiting your review of this table and the tag-mapping proposal before Phase 2.**
