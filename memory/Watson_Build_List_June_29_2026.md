# Watson Build List
*As of June 29, 2026*

---

## Completed — June 29, 2026

- ✅ missed_report.py — `member_status` filter added (excludes deceased/disconnected/non_local/snowbird)
- ✅ campus_classifier.py — new job, Mon 5:45am, classifies all active members from 8-week connect card history; logic: both ≥2 → Hybrid; either ≥5 → that campus; majority wins middle zone; Wilmington tiebreak/default
- ✅ missed_report.py — three campus sections (Wilmington / Online / Hybrid); each section suppressed if empty
- ✅ Dashboard member management — campus dropdown (Wilmington/Online/Hybrid) added to member expand panel
- ✅ app.py PATCH `/api/members` — `campus_preference` added to allowed fields

---

## Active Bugs

1. Telegram "View on Dashboard" Dev Loop link opens `/#devloop` tab but doesn't deep-link to specific project
2. "Send to Claude Code" button — legacy button in dashboard, not yet removed
3. KB search (`qwen2.5-coder:7b`) — timed out at 14 min during testing; root cause unresolved
4. `/draft` page UI copy — may still say "Pushing to GitHub…" — verify and update to "Queuing…"

---

## Phase 1 — Quick Wins

1. `[assist]` Auto-generate social captions on blog publish — hook to `scheduler.py`, Telegram approval, 4 options via Ollama, queue to `facebook_queue`

---

## Phase 2 — New Jobs, Clear Path

2. `[assist]` Morning briefing auto-push — no manual Telegram command needed
3. `[assist]` Weekly email draft pipeline — briefing email button → article links → Watson drafts Kit email → approval
4. `[assist]` Follow-up reminders — "Follow up with Dave in 3 days" → Watson schedules nudge
5. `[assist]` Context-triggered reminders — "Remind me about X when I talk to Y" → fires on calendar match
6. `[assist]` `/menu` Telegram command — exists, needs review and update
7. `[assist]` People Registry — add a person via Telegram (lookup already built)
8. `[assist]` Weekly email end-to-end test

---

## Phase 3 — Heavier Builds

9. `[assist]` FMS social feed — trending apologetics content → draft FB posts → approval gate → post
10. `[assist]` Catchall email — `watson@williamckyomes.com` + `watson@faithmakessense.com`
11. `[assist]` Book development job — `jobs/book/research_brief.py`

---

## Writing & Publishing

12. `[writing]` ARC welcome email — Watson detects new Kit signup, sends welcome automatically
13. `[writing]` ARC weekly digest — feedback summary → Telegram or email to Bill
14. `[writing]` TWJ provisioning — ARC readers → credentials + personalized Kit emails at launch
15. `[writing]` TWJ launch page — pending: `KIT_API_KEY` + `KIT_TWJ_TAG_ID` Vercel env vars, `GIVEBUTTER_LINK`, `AMAZON_LINK`, flip `AMAZON_LIVE=true` at preorder

---

## Adelphos Academy

16. `[adelphos]` Moodle REST API integration — auth, token, base client in `jobs/adelphos/`
17. `[adelphos]` Lesson builder — `Watson build lesson [course] [title]` → Google Doc → Moodle
18. `[adelphos]` Quiz generator — Watson drafts questions → approval → publishes
19. `[adelphos]` Course spec system — one Google Doc per course defines module structure
20. `[adelphos]` Weekly monitoring digest — enrollment, completion rates, inactive students → Telegram
21. `[adelphos]` Student stuck alert — no progress in X days → sends encouragement email
22. `[adelphos]` Course announcement emails — Watson sends via Moodle when new content publishes
23. `[adelphos]` Student welcome message — auto on enrollment

---

## Ministry & Infrastructure

24. `[assist]` GitHub token renewal — `watson-all` may be expired; renew and update SECRETS.md + `.env` on Beelink
25. `[assist]` FMS site rebuild — `~/fms`, Next.js App Router, all data through Watson; not yet started
26. `[assist]` Transcription backlog — 10 years of sermon audio on FMSPC

---

## Tag Key

- `[assist]` — Watson assistant/automation tasks
- `[writing]` — Book and publishing pipeline tasks
- `[adelphos]` — Adelphos Academy / Moodle integration tasks
