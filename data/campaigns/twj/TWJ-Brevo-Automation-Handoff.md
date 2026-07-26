# The Wrong Jesus — 8-Week Launch: Automation Handoff for Dev Project

*Prepared for Claude Code / Watson wiring. Pairs with `TWJ-Launch-Full-Copy-Weeks-1-8-CORRECTED.md` (full copy, section headers match the schedule below) and the WCKY Book Launch Framework.*

**Platform:** All sends now go through Brevo (replaces Kit and Gmail SMTP). Facebook posts continue to publish via Watson's existing Facebook posting skill — confirm it's current before Week 1 posts fire.

**Launch date:** Tuesday, September 15, 2026
**Runway:** 8 weeks, Wednesday-to-Tuesday cycles, starting Wednesday, July 22, 2026

---

## BREVO CONTACT SEGMENTS NEEDED

Set these up in Brevo before scheduling any sends:

1. **General list** — full WCKY audience (replaces old Kit master list)
2. **Donor tag** — active FMS monthly donors (replaces Givebutter separate list; content still donor-specific, just same platform now)
3. **ARC tag** — confirmed ARC readers (separate from Donor tag; do not merge — ARC and donor status are tracked independently even though some people may hold both)

---

## SEND SCHEDULE

| Date | Day | Platform | Segment/Tag | Content Reference |
|---|---|---|---|---|
| Jul 22 | Wed | Facebook | Public | Week 1, FB Post 1 |
| Jul 23 | Thu | Brevo | General | Week 1, Brevo Email — "Something I've been working on" |
| Jul 24 | Fri | Brevo | Donor | Week 1, Brevo Email (Donor segment) — "A gift for you when the book drops" |
| Jul 25 | Sat | Facebook | Public | Week 1, FB Post 2 |
| Jul 29 | Wed | Facebook | Public | Week 2, FB Post 1 |
| Jul 30 | Thu | Brevo | General | Week 2, Brevo Email — "The question this book answers" |
| Aug 1 | Sat | Facebook | Public | Week 2, FB Post 2 |
| Aug 5 | Wed | Facebook | Public | Week 3, FB Post 1 |
| Aug 6 | Thu | Brevo | General | Week 3, Brevo Email — "A story from the book" |
| Aug 7 | Fri | Brevo | Donor | Week 3, Brevo Email (Donor segment) — "Field update + book news" |
| Aug 8 | Sat | Facebook | Public | Week 3, FB Post 2 |
| Aug 12 | Wed | Facebook | Public | Week 4, FB Post 1 (ARC close announcement) |
| Aug 13 | Thu | Brevo | General | Week 4, Brevo Email — "The date is set" |
| Aug 14 | Fri | Brevo | ARC | Week 4, ARC Reader Email — "The date is set. Your review matters." |
| Aug 15 | Sat | Facebook | Public | Week 4, FB Post 2 (launch date reveal) |
| Aug 19 | Wed | Facebook | Public | Week 5, FB Post 1 |
| Aug 20 | Thu | Brevo | General | Week 5, Brevo Email — "What people are saying" |
| Aug 21 | Fri | Brevo | Donor | Week 5, Brevo Email (Donor segment) — "One week out" *(note: label is legacy from 6-week framework — content is fine, just not literally "one week out" at this point in the 8-week calendar; consider retiming this beat, see Open Questions)* |
| Aug 22 | Sat | Facebook | Public | Week 5, FB Post 2 |
| Aug 26 | Wed | Facebook | Public | Week 6, FB Post 1 |
| Aug 27 | Thu | Brevo | General | Week 6, Brevo Email — "Almost here" |
| Aug 29 | Sat | Facebook | Public | Week 6, FB Post 2 |
| Sep 2 | Wed | Facebook | Public | Week 7, FB Post 1 |
| Sep 3 | Thu | Brevo | General | Week 7, Brevo Email — "This time next week" |
| Sep 4 | Fri | Brevo | Donor | Week 7, Brevo Email (Donor segment) — "Final shipping confirmation" |
| Sep 5 | Sat | Facebook | Public | Week 7, FB Post 2 |
| Sep 15 | Tue | Facebook | Public | Week 8, FB Post 1 — morning, launch |
| Sep 15 | Tue | Brevo | General | Week 8, Brevo Email — "It is live" |
| Sep 15 | Tue | Brevo | Donor | Week 8, Brevo Email (Donor segment) — "Your signed copy is on its way" |
| Sep 15 | Tue | Brevo | ARC | Week 8, ARC Reader Email — "Launch day. Thank you." |
| Sep 15 | Tue | Facebook | Public | Week 8, FB Post 2 — evening, thank you |
| Sep 18 | Fri | Facebook + Brevo | Public / General | Day 3 post-launch — FB post + Brevo email "In case you missed it" |

---

## OPEN QUESTIONS FOR BILL BEFORE FINAL SCHEDULING

1. **Week 5 donor email timing** — the "One week out" shipping-confirmation email lands Aug 21, which is three weeks before launch, not one. This beat may need to move later (e.g., into Week 7, alongside or replacing "Final shipping confirmation") rather than run twice with the same framing. Flag for review.
2. **ARC access shutdown** — the password-protected `/room` page needs to be deactivated at end of day September 15, accounting for time zones. Confirm this is scripted as part of the Sep 15 job, not a manual step.
3. **Placeholders** — `GIVEBUTTER_LINK` and `AMAZON_LINK` on the launch page still need to be populated before Sep 15; `AMAZON_LIVE` flag needs activation. These aren't part of the send automation but are hard blockers for the Week 8 links to work.

---

## IMAGE ASSETS NEEDED (recap)

See the Image Plan section at the top of `TWJ-Launch-Full-Copy-Weeks-1-8-CORRECTED.md` for the full template breakdown. Summary: Bill will drop these into a folder for Watson to composite from —
1. Book cover (`wrong-jesus-cover-iso.png`, already in site media library)
2. 2–3 background textures/colors
3. Wordmark/logo mark
4. Quotation mark graphic element
5. Buy-now/arrow/button graphic
6. Optional author photo

Watson generates the 17 Facebook post images from these elements per the template mapping, rather than needing 17 unique custom images.

---

## FILES IN THIS HANDOFF

- `TWJ-Launch-Full-Copy-Weeks-1-8-CORRECTED.md` — full copy for every post/email, plus image template plan
- `TWJ-Launch-Outline-Weeks-1-8.md` — structural skeleton (useful for the next book's template)
- `WCKY-Book-Launch-Framework.md` — source framework this launch is built from
- This document — dated schedule, segment mapping, and open items
