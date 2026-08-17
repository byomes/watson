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

## Phase 2 build — findings from Bill's pre-`--live` verification (2026-08-17)

Before either script could be approved to run `--live`, two things needed
checking against real docs, not assumption:

**1. Kit's `state` enum, confirmed via Kit developer docs and help center:**
exactly five values — `active`, `cancelled`, `bounced`, `complained`,
`inactive`. `inactive` is engagement-based (no opens/clicks in ~90 days),
not an opt-out. `kit_suppression_export.py`'s original bucketing (`state !=
"active"`) wrongly swept `inactive` in as suppressed — **fixed**:
`_SUPPRESSED_STATES = {"cancelled", "bounced", "complained"}` only,
`inactive` and any unrecognized state tracked separately in stats for
visibility, never treated as a suppression.

**2. Brevo's `emailBlacklisted` — confirmed via Brevo's own API reference
docs (`developers.brevo.com`) plus an official Brevo staff reply on their
community forum, not assumption. Finding is more consequential than the
original worry:**

Brevo has two separate, unrelated blocking fields on a contact:
- `emailBlacklisted` (boolean) — governs Brevo's native **Campaigns/
  Automation** blocklist only. Does *not* block transactional sends.
- `smtpBlacklistSender` (array of sender addresses) — a *separate* field
  that blocks **transactional** sends, scoped per-sender. (Brevo staff,
  community forum: "You can use the parameter 'smtpBlacklistSender' in the
  Create Contact and Update Contact endpoints" to block transactional
  email specifically.)

So `emailBlacklisted` does **not** put ARC/Writing Room password resets at
risk (resolves the original worry) — but it also means **it has no effect
on anything Watson actually sends**, because `brevo_send.py` sends
everything (password resets, welcome emails, *and* the book-launch
campaign dispatcher's newsletter-style sends) through Brevo's
**transactional** `/v3/smtp/email` API. Watson never uses Brevo's native
Campaigns/Automation feature, which is the only thing `emailBlacklisted`
governs. Applying it to former Kit subscribers would silently be a no-op
against Watson's real send path — the suppression transfer wouldn't
actually suppress anything.

The alternative, `smtpBlacklistSender`, has the opposite problem: it blocks
*all* transactional mail from a given sender, and Watson sends both
newsletter-style and account-necessary mail (password resets, welcome
emails) from the same sender through the same API — scoping it to Watson's
sender would block account-necessary mail too, which is the exact failure
mode Bill flagged.

**Root cause: Brevo has no primitive that maps onto "suppress newsletter-
style sends but keep account-necessary sends flowing," because that
distinction exists only in Watson's own code (which handler calls
`brevo_send.py`), not in anything Brevo can see at the API/sender level.**

**Flagged back to Bill rather than picked unilaterally.** Options surfaced,
none built: (a) enforce suppression in Watson's own dispatch code — check
a contact's suppressed status before any *newsletter-type* send, leave
Brevo's own blocklist fields untouched entirely; (b) a second, dedicated
Brevo sender address used only for newsletter-style sends, then
`smtpBlacklistSender` scoped to that sender only; (c) something else Bill
decides. **`brevo_suppression_import.py`'s mechanism (`emailBlacklisted`)
is unchanged pending that decision** — not committed as a resolved
approach.

## Phase 2 resolution (2026-08-17) — option (b) tried, then reverted same day

Bill initially decided on **native Brevo mechanism, option (b)** — a second
dedicated verified sender, `newsletter@williamckyomes.com`, scoped so
`smtpBlacklistSender` couldn't also block account-necessary transactional
mail. Built and documented (see prior revision of this section in git
history), then **reverted the same day** before Phase 2 was committed —
see below.

### Final decision: suppression runs against `watson@williamckyomes.com` directly

Bill reverted the second-sender approach. **`smtpBlacklistSender` is now
scoped to `DEFAULT_FROM_EMAIL` (`watson@williamckyomes.com`) — Watson's
existing, already-verified sender — not a dedicated newsletter address.**

**This is a deliberate, accepted tradeoff, not an oversight:**
`smtpBlacklistSender` blocks per-sender, and Watson sends *everything*
(newsletter-style broadcasts and account-necessary transactional mail —
password resets, welcome emails) from that one address. So a contact
suppressed for opting out of / bouncing on the newsletter will also stop
receiving transactional mail from Watson. **Accepted as low-risk** because
the suppression population is built from bounced/complained Kit
addresses, which are unlikely to also need transactional mail from Watson
— but the risk is real and should not be quietly rediscovered later by
whoever touches this next. If Watson ever needs transactional delivery to
keep working independent of newsletter opt-out status, the fix is to
revisit the dedicated-second-sender approach (option (b) above), not to
assume this file's silence means it was never considered.

**Request shape** (`POST /v3/contacts`, `updateEnabled: true`):
```
{"email": ..., "updateEnabled": true,
 "smtpBlacklistSender": ["watson@williamckyomes.com"], "emailBlacklisted": true}
```
Both fields still set for the same reasons as before: `smtpBlacklistSender`
is the field that actually affects Watson's real (transactional) send
path; `emailBlacklisted` is a no-op against that path today but accurately
records opt-out status and future-proofs for Brevo's native Campaigns
feature if anything ever sends through it.

**Known doc gap, unchanged from before:** neither Brevo reference page
states whether a repeat write REPLACES or MERGES the existing
`smtpBlacklistSender` array, and `GET /v3/contacts/{id}` doesn't expose
the array back for a read-before-write check. Since
`watson@williamckyomes.com` is Watson's *only* sender, a repeat run
against an already-suppressed contact is expected to be idempotent in
practice (same single-element list re-written) — but the REPLACES-vs-
MERGES behavior itself is still unconfirmed by Brevo's docs. Worth
confirming with Brevo support before ever adding a second sender to this
list in the future.

**Code changes (final state):**
- `jobs/email_job/brevo_send.py` — `NEWSLETTER_FROM_EMAIL` constant
  removed; not needed. Only `DEFAULT_FROM_EMAIL`
  (`watson@williamckyomes.com`) remains.
- `jobs/migration/brevo_suppression_import.py` — `apply_suppression()`
  sends `smtpBlacklistSender: [DEFAULT_FROM_EMAIL]` + `emailBlacklisted:
  true` together, importing `DEFAULT_FROM_EMAIL` from `brevo_send.py`.

**`jobs/email_job/draft_email.py` — still out of scope, deferral
unchanged** (per Phase 1 decisions above; unaffected by the sender
revert).

**`jobs/campaigns/brevo_dispatcher.py` / TWJ launch campaign — unaffected,
no scope change.** It already sends from `DEFAULT_FROM_EMAIL`, which is
now also the suppression-scoped sender, so no revisit is needed here (the
prior "revisit after launch" note is moot now that there's only one
sender).

**Note for whoever wires up Comms Desk's general-comms broadcast sends:**
those sends should also go through `DEFAULT_FROM_EMAIL` (the only sender
Watson has) and will be subject to the same accepted transactional/
newsletter suppression overlap described above. Not built here — flagged
so the constraint isn't rediscovered from scratch.

## Phase 3 hard requirement (2026-08-17) — suppression-preserving upserts

**`brevo_import.py` MUST NOT ever write a contact upsert that can silently
clear the `smtpBlacklistSender`/`emailBlacklisted` fields Phase 2 set.**
This is a hard requirement for the Phase 3 build, not a nice-to-have.

**What was checked, via Brevo's docs first:** fetched
`developers.brevo.com/reference/createcontact`,
`/reference/updatecontact`, and `/docs/synchronise-contact-lists`
directly. **None of the three documents whether an upsert (`POST
/v3/contacts` with `updateEnabled: true`, or `PUT
/v3/contacts/{identifier}`) merges or replaces fields the payload omits**
— for `attributes`, `emailBlacklisted`, or `smtpBlacklistSender` alike.
No exact wording addresses this either way.

**What IS confirmed, from Brevo's own community forum** (staff reply from
"adam" on
`community.brevo.com/t/updating-a-multi-value-field-of-a-contact-via-api-results-in-loosing-the-existing-value/6701`):
updating a contact's **multi-value (array) field** via the API **replaces**
the array wholesale — the existing values are lost unless the request
explicitly re-includes them ("you would need to add the existing
conditions too"). No merge/append exists for array fields today.
`smtpBlacklistSender` is the same array-of-strings shape as the
multi-value field in that confirmed report, so the same replace behavior
is the reasonable read even though Brevo's reference pages don't name
`smtpBlacklistSender` specifically. Per the standing rule for this
migration — **undocumented is treated as "will clear," not assumed
safe** — this is the design basis below.

**Design requirement this imposes on `brevo_import.py`:** before every
upsert, check the contact's email against the most recent
`kit_suppression_*.json` snapshot (the same file `brevo_suppression_import.py`
consumes in Phase 2). If the email is in that suppressed set, the upsert
payload MUST explicitly include
`"smtpBlacklistSender": [DEFAULT_FROM_EMAIL], "emailBlacklisted": true`
alongside whatever attributes/list-membership fields that contact's Kit
tags produce — every single time, on every run, not just the first.
Silently omitting those two fields on a Phase 3 re-run (e.g. after a Kit
tag changes and the contact needs updating) is exactly the scenario this
guards against: it would undo Phase 2's suppression work with no error,
no log, and no signal that it happened. Contacts NOT in the suppressed set
need no special handling — they had nothing to preserve.
