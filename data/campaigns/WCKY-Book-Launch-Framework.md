# WCKY Book Launch Framework
### A Repeatable Process for Every Book Release
### v2 — updated 2026-07-25 to reflect the Brevo-based automation system

---

## WHAT CHANGED FROM v1

- **Kit and Givebutter are no longer separate email platforms for launch sends.** All transactional and campaign email — general list, donor list, ARC list — now goes through **Brevo**, using the single verified sender `Watson <watson@williamckyomes.com>`. Kit remains the source of the general subscriber list for now (a one-time export per campaign), with full Kit retirement planned as a separate future project.
- **The entire 6-week (or extended) calendar is now a real system**, not a manual checklist: `book_launch_campaigns`, `book_launch_sends`, and `book_launch_contacts` tables in `watson.db`, driven by `jobs/campaigns/`. See "THE AUTOMATION SYSTEM" section below for how to run this for the next book.
- **Weekly human approval is required by design** — nothing sends without an explicit approve step (dashboard or Telegram). This isn't a limitation to remove later; it's intentional, based on a real incident during this system's first build (see Lessons Learned).

---

## PROGRAM OVERVIEW

**FMS Partner Benefit (Permanent)**
Every recurring monthly donor to Faith Makes Sense receives a signed copy of every new WCKY book at launch. This is not a one-time promotion. It is a standing donor benefit that compounds in value with every book released.

**Launch Runway:** 6 weeks pre-launch (extendable — TWJ ran 8 weeks; treat 6 as the default, not a hard rule)
**Primary Social Channel:** FMS Facebook Page
**ARC Window:** Opens Week 1, closes 2 weeks before launch day, and now **auto-expires on launch day** via the ARC portal's built-in shutdown check (no manual step required)
**Audiences:** WCKY email list (Kit export → Brevo "general" segment), FMS donors (Givebutter → Brevo "donor" segment, live-synced), ARC readers (Brevo "arc" segment, live-queried)

**ARC vs. FMS Benefit (Important Distinction — unchanged from v1)**
ARC readers receive the digital copy only. The signed physical copy is exclusive to FMS monthly donors. These are separate programs solving separate problems. ARC readers get early access and public recognition. FMS donors get a tangible, personal gift that grows in value with every book. Do not conflate the two. ARC readers are a donor pipeline — not a donor substitute.

---

## THE SIX-WEEK LAUNCH CALENDAR

*(Unchanged in structure from v1 — only the platform names below reflect current reality.)*

### WEEK 1: SEED
*Goal: Plant the idea. Generate curiosity. Open ARC.*

**FMS Facebook**
- Post 1: Personal announcement. The book exists. What it is and why it matters. No links yet.
- Post 2: Behind the book. One paragraph on what prompted it. First-person, honest, not promotional.

**Email: General segment (Brevo)**
- Subject: Something I've been working on
- Content: Informal announcement. What the book is. What problem it solves. ARC reader recruitment CTA.

**Email: Donor segment (Brevo)**
- Subject: A gift for you when the book drops
- Content: Announce the permanent signed copy benefit. Frame it as gratitude, not incentive. No ask.

**ARC**
- Open ARC recruitment. Direct readers to evergreen /arc page on williamckyomes.com.
- Upon signup, ARC readers receive a password and link to a password-protected page where the digital copy is accessible.
- Access closes automatically on launch day (system-enforced — see Automation section).

---

### WEEK 2: BUILD
*Goal: Establish the problem the book solves. Make people feel the need.*

**FMS Facebook**
- Post 1: The problem. What happens when people get this wrong. No solution yet. Let the tension sit.
- Post 2: A quote or excerpt from the book. One paragraph. Pull something that stops the scroll.

**Email: General segment (Brevo)**
- Subject: The question this book answers
- Content: One core question the book addresses. Brief. Drive to /arc page for late signups.

---

### WEEK 3: DEEPEN
*Goal: Build credibility and connection. Show, do not tell.*

**FMS Facebook**
- Post 1: One story from the book (fictionalized or illustrative if needed). Human, specific, pastoral.
- Post 2: Why this matters for ordinary people. Not scholars. Not pastors. Regular people.

**Email: General segment (Brevo)**
- Subject: A story from the book
- Content: Share the same story in slightly expanded form. Soft CTA to watch for launch date.

**Email: Donor segment (Brevo)**
- Subject: Field update + book news
- Content: Short East Africa update first. Then remind them of the signed copy benefit. No new ask.

---

### WEEK 4: ARC CLOSE + PIVOT TO LAUNCH
*Goal: Close ARC. Begin building launch anticipation.*

**FMS Facebook**
- Post 1: ARC close announcement. Last call for review copies.
- Post 2: Launch date reveal. Clean, clear, no over-hype.

**Email: General segment (Brevo)**
- Subject: The date is set
- Content: Launch date. What to expect. How to pre-order or be notified. One clear CTA.

**Email: ARC segment (Brevo)**
- Subject: The date is set. Your review matters.
- Content: Remind them of the launch date. Note that password-protected access closes on launch day — this is their last window to finish reading. Ask them to post their review on Amazon and Goodreads on launch day. Include direct links. Make it easy. Close with one soft paragraph: if this book has meant something to them, invite them to consider becoming an FMS monthly partner. One sentence. One link (Givebutter). No pressure.

---

### WEEK 5: MOMENTUM
*Goal: Build urgency without pressure. Let social proof begin.*

**FMS Facebook**
- Post 1: Early ARC feedback (with permission). Real words from real readers.
- Post 2: Remind FMS donors of the signed copy benefit. Soft new donor recruitment.

**Email: General segment (Brevo)**
- Subject: What people are saying
- Content: 2-3 ARC reader quotes. Brief framing. Drive to pre-order or launch day reminder.

**Email: Donor segment (Brevo)**
- Subject: (avoid "one week out" / countdown framing unless this email genuinely lands in the final week — check the actual date against launch_date before writing)
- Content: Reminder of the signed copy benefit. Confirm shipping address if not already collected. Warm, grateful tone.

---

### WEEK 6 (FINAL WEEK): LAUNCH
*Goal: Convert attention into purchases and new donors.*

**LAUNCH DAY**

FMS Facebook
- Post 1 (morning): It is here. Clean image. Purchase link. FMS partner CTA.
- Post 2 (evening): Thank you post. Name ARC readers publicly (with permission). Celebrate.

Email: General segment (Brevo)
- Subject: It's live
- Content: Direct. The book is available. Here is where to get it. Here is how to become an FMS partner and receive a signed copy.

Email: Donor segment (Brevo)
- Subject: Your signed copy is on its way
- Content: Confirm they are receiving a copy. Thank them. Remind them what their giving funds in East Africa.

Email: ARC segment (Brevo)
- Subject: Launch day. Thank you.
- Content: Thank them. Remind them to post their review today if they have not already. Include direct links again. Close with one line: if they want to go deeper into the work behind the book, here is how to partner with FMS. Link only. No explanation needed at this point.

**DAY 3 POST-LAUNCH**

Email: General segment (Brevo)
- Subject: In case you missed it
- Content: Brief follow-up. Any early momentum (reviews, feedback). Repeat purchase CTA.

FMS Facebook
- Post: Share a review or reader response received since launch.

---

## LINK STRATEGY — IMPORTANT

**Do not embed raw purchase/donation links directly in Facebook post or email copy.** Route all "buy now" / "learn more" CTAs through the book's own launch page (e.g. `williamckyomes.com/[book-slug]`) instead. That page carries the real Amazon and Givebutter links, and can be updated once, centrally, if a link changes or isn't ready yet at write time — rather than needing every already-published post or sent email retroactively fixed. Build this launch page in Week 0 (before Week 1 starts), even with placeholder purchase links, so all copy can safely reference it from day one.

---

## FMS DONOR BENEFIT: FULFILLMENT PROCESS

Use this checklist for every book launch:

- Givebutter export: pull list of all active monthly donors 30 days before launch
- Collect shipping addresses: send dedicated email 3 weeks before launch with address confirmation link
- Order author copies from KDP: calculate quantity based on donor count plus buffer
- Sign and package: personal note inside each copy recommended
- Ship within 7 days of launch date
- Send shipping confirmation email to each donor

New donors who join during the launch window (up to 30 days post-launch) also receive a signed copy. Communicate this clearly in launch messaging.

---

## PLATFORM ROLES (updated)

| Platform | Audience | Purpose |
|---|---|---|
| FMS Facebook | Public / cold audience | Awareness, launch announcements, social proof |
| Brevo — general segment | WCKY list / warm audience | Depth, story, conversion |
| Brevo — donor segment | Active FMS donors | Retention, fulfillment, gratitude |
| Brevo — arc segment | Recruited reviewers | Reviews, credibility, word of mouth, donor pipeline |
| williamckyomes.com/arc | All | Evergreen ARC signup page |
| williamckyomes.com/[book-slug] | All | Central launch page — carries the real purchase/donate links, referenced by every CTA |

---

## THE AUTOMATION SYSTEM — HOW TO RUN THIS FOR THE NEXT BOOK

1. **Write the copy.** Produce a full-copy markdown doc following this framework's week/post/email structure, with consistent section headers (`## WEEK N`, `### FB Post N (image: [template type])`, `### Brevo Email — "Subject"`, `### Brevo Email (Donor segment) — "Subject"`, `### ARC Reader Email — "Subject"`) — the parser depends on this exact structure.
2. **Write the dated schedule** as a separate handoff doc mapping each item to a real calendar date.
3. **Drop both files** into `~/watson/data/campaigns/[book-slug]/` on the Beelink.
4. **Run the dry-run parser** (`jobs/campaigns/book_launch_parser.py`, dry-run mode) and review the preview output before anything is written to the database — check for parsing ambiguities the same way the first run surfaced 7 real flags worth resolving by hand.
5. **Run Phase 1B** to actually create the campaign row and insert all parsed sends, plus the one-time Kit export for the general segment (donor and ARC segments query live tables automatically — no export needed for those).
6. **Prepare image assets** for the 8 reusable Facebook templates (cover+tagline, quote, personal/behind-scenes, problem/tension, date/countdown, benefit/CTA, testimonial, gratitude) — same asset checklist as any prior launch, just swap in the new book's cover and any book-specific graphics.
7. **Each week**, a Telegram digest arrives automatically (Sunday 6pm cron) previewing the coming week's sends, with links to a dashboard editor (admin-authenticated) and a one-tap Approve. **Nothing sends without this explicit approval — this is intentional, not a bug to remove.**
8. **Manually set ARC portal access dates in the wcky repo.** Update `ARC_MANUSCRIPT_UNLOCK`/`ARC_MANUSCRIPT_CLOSE` in `~/wcky/src/lib/launch-dates.ts` to match the new launch — this is a hardcoded constant, not wired to Watson's `book_launch_campaigns.launch_date`, so it does not update automatically just because a new campaign row is created.

---

## LESSONS LEARNED (carry forward to every future launch)

- **Test data must be structurally incapable of triggering real actions**, not just cleaned up after. The dispatch system now independently verifies a campaign is real and active before any Facebook queue write or Brevo send happens — regardless of what flag a test script passes. This exists because an early test run briefly posted two "test post" entries to the live FMS Facebook page before this safeguard was added. Don't relax this "belt and suspenders" check in future work on this system, even if it feels redundant.
- **Route CTAs through a launch page you control**, not directly to Amazon/Givebutter in copy — see Link Strategy above.
- **Kit has no true "general list" object** — if using Kit again for a future export, the account-wide subscriber total (not any single tag or form) is the correct "general" figure, since Kit doesn't distinguish a master list from its narrower tags/forms.
- **Donor segment = `donors.db` excluding lapsed donors** (this codebase's existing definition of "active"). ARC segment = `arc_readers` filtered to active status AND matched to the specific book by slug — this second filter matters once more than one campaign's ARC readers exist in the same table.
- **ARC manuscript close date is a hardcoded constant, not dynamic.** The ~/wcky repo's ARC_MANUSCRIPT_CLOSE (in src/lib/launch-dates.ts) does NOT automatically read from Watson's book_launch_campaigns.launch_date — they're two separate systems that happen to agree for this launch because someone set them to match manually. For the next book launch, remember to update ARC_MANUSCRIPT_UNLOCK/ARC_MANUSCRIPT_CLOSE in the wcky repo by hand — this doesn't happen automatically just because a new campaign row is created in Watson.

---

## ASSETS NEEDED FOR EVERY LAUNCH

- Final book cover image (high resolution)
- Author copy confirmed before launch date
- Password-protected ARC page on williamckyomes.com (built and tested before Week 1)
- ARC access password to distribute to confirmed readers
- Givebutter monthly giving campaign link
- Amazon and Goodreads purchase links
- Launch page (williamckyomes.com/[book-slug]) built and live before Week 1, even with placeholder purchase links
- Shipping address collection form or link
- 8 Facebook post template image assets (see automation system, step 6)
- Full copy doc + dated schedule doc, following this framework's structure exactly

---

## SCALING NOTES

This framework is designed to run with one person, with Watson handling scheduling, sending, and posting under weekly human approval. As the book pipeline grows and the FMS donor base grows, the signed copy program becomes the anchor retention benefit for the entire ministry funding model. Every new book strengthens the reason to stay a monthly partner.

Future additions to consider:
- Dedicated launch team (5-10 volunteers for day-of amplification)
- Podcast or interview appearances in Weeks 4-5
- Church bulletin and pulpit announcement at Catalyst in Week 5
- Goodreads author page integration
- Full Kit retirement — migrate general-list subscriber management natively into Brevo rather than a one-time export per launch
