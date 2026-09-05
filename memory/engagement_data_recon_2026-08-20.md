# Engagement Data Recon — 2026-08-20

Read-only exploration of two new data sources Watson now has Viewer access to:
1. Google Sheet `1CYm6rqvyO-ELWnHiElwJJjNA-df30ExZvxJZFILw7qw` ("Catalyst Tracking Sheet - Communications Team")
2. GA4 property `509598079` via the Analytics Data API v1

**This is discovery only.** No sync jobs, no new `watson.db` tables, no new cron entries were built. Credential used: the existing `watson-sheets-reader@watson-498401.iam.gserviceaccount.com` service account, same pattern as `jobs/gsheets/headcount_sync.py` (`config/sheets_service_account.json`, or `GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE` env override). No new service account or credential file was created. `google-analytics-data` was pip-installed into the venv to make the GA4 calls for this recon — it is **not** yet added to `requirements.txt`, since nothing permanent depends on it yet.

---

## Part 1 — Google Sheet structure

**Spreadsheet title:** "Catalyst Tracking Sheet - Communications Team"
**Tabs:** 2 total — `2025`, `2026`. No other tabs (no hidden/empty tabs found).

### Shape — not row-per-record

This sheet is **not** a typical "one row per date" table. It's a manually-curated monthly digest: each row is a named metric (e.g. "Facebook Followers", "Active Web Users"), and each column is a calendar month, with `TOTAL` and `YTD AVG` summary columns at the end. Grouped into labeled sections with blank-row separators:

- **Connect Cards/Registration** — Digital Wilmington Connect Cards Completed, Online Connect Cards Completed, Registration Forms Open/Completed (2025 tab uses "Digital Wilmington" + "Online" as two separate rows; 2026 tab collapses to one "Connect Cards Completed" row — **inconsistent row structure between the two tabs**, not just inconsistent values)
- **Social Media** — Facebook Followers/Posts/Likes/Shares, Instagram Followers/Posts/Likes/Shares
- **Catalyt App Engagement** *(sic — misspelled "Catalyst" both tabs)* — App Downloads, App Impressions, App Launches
- **E Mails/Website** — Email Campaigns Sent, Total Emails Sent, Emails Opened, Email Links Clicked, **Active Web Users, New Web Users, Avg Engagement Time (seconds), Event Count**
- **Aquisitions** *(sic — misspelled "Acquisitions" both tabs)* — Direct Link / Organic Search / Social-Referrals, as percentages
- **Top Page Views** — ranked 1st–5th, each cell a freeform string combining page name + percentage (e.g. `"Home Page 29%"`), not split into separate name/value columns

### Per-tab detail

| Tab | Months populated | Rows with data | Declared grid size |
|---|---|---|---|
| `2025` | Oct, Nov, Dec 2025 only | 44 | 997 rows × 26 cols (mostly empty — template oversized for a 3-month year) |
| `2026` | Jan–Jun 2026 filled; Jul–Dec columns exist but are **empty** | 43 | 996 rows × 26 cols |

There is no date column in the usual sense — "date range covered" is really "which month-columns have data": **Oct 2025 – Jun 2026**, with a roughly 2-month reporting lag (today is 2026-08-20 and July/August 2026 aren't filled in yet — consistent with a person manually updating this, not automation).

### Oddities

- **No single header row.** Row 1 has month names as column headers, but section labels ("Social Media", etc.) are their own header-like rows interleaved with data rows — a simple "row 1 = headers" parser would break immediately.
- **Row structure differs between tabs**: 2025 splits Wilmington vs. Online connect cards into two rows; 2026 merges them into one "Connect Cards Completed" row. A sync job can't assume stable row labels year-over-year.
- **Inconsistent number formatting**: thousands separators are inconsistent even within the same column (`"1465"` vs `"1,474"` in adjacent months, same row, `2026` tab, Facebook Followers).
- **Inconsistent percentage precision**: `"84.00%"` vs `"87%"` vs `"49.50%"` — no fixed decimal convention.
- **`"TBD"` as a data value** — 2025 tab, "New Web Users", October: literal string `"TBD"` sitting in an otherwise-numeric column.
- **Spurious near-empty row** in the 2026 tab between "Event Count" and the "Aquisitions" section header — all blank cells except a stray `"0"` in the YTD AVG column, almost certainly a leftover SUM/AVERAGE formula artifact.
- **"Top Page Views" values aren't machine-parseable** without a regex split (page name and percentage are concatenated in one cell, and page names are inconsistent — `"Home Page"` in some months, `"Home"` or `"Home -"` in others for what's presumably the same page).
- **Two misspelled section labels** ("Catalyt", "Aquisitions") — cosmetic, but would break a naive column/section-name match if one were built.
- **Direct overlap with GA4**: "Active Web Users", "New Web Users", "Avg Engagement Time (seconds)", "Event Count" (E Mails/Website section) and the "Aquisitions" percentages (Direct Link/Organic Search/Social-Referrals) look like hand-copied GA4 numbers — see "duplicate tracking" note in Part 3. They won't reconcile exactly with a live GA4 pull (see Part 2) since GA4's own historic data is subject to retention/sampling and the sheet's copy is a frozen snapshot from whenever it was transcribed.

---

## Part 2 — GA4 property 509598079

### Metadata (`getMetadata`)

- **375 dimensions, 101 metrics** available — this is the full stock GA4 catalog, not a filtered one.
- **Only 3 "custom"-named dimensions**, all Google Ads-related (`firstUserGoogleAdsCustomerId`, `googleAdsCustomerId`, `sessionGoogleAdsCustomerId`) — **no actual custom dimensions or custom metrics have been configured** for this property. No custom events beyond GA4's auto-collected defaults are being tracked.
- Metric/dimension categories present: Attribution (83 dims, mostly ad-platform-specific — CM360, SA360, Google Ads — all irrelevant here), Ecommerce (22 dims / 29 metrics — irrelevant, no online store), Cohort, Demographics, Geography, Page/Screen, Platform/Device, Publisher (AdSense-style — irrelevant), Time, Traffic Source, User.
- **"conversions" as a bare metric name no longer exists** in this API version — it's been renamed `keyEvents` (with per-event breakdowns like `keyEvents:purchase`). Used `keyEvents` instead of `conversions` for the exploratory pull below, per the task's "if configured" caveat.

### Earliest real traffic

Queried daily `sessions`/`totalUsers` from 2020-01-01 to today. **304 rows returned, earliest = 2025-10-21.** That's ~304 calendar days between 2025-10-21 and 2026-08-20 — traffic exists on essentially every day in that window, no real gaps. **This property has no usable history before 2025-10-21** — treat that as the practical start date for any trend report, not "since GA4 was enabled" (which may be earlier but with no data flowing).

### Weekly trend, last 90 days (14 weeks, `yearWeek` grain)

All 14 weeks have real, non-zero data. Representative range across the period:
- `totalUsers`: 39–345/week
- `sessions`: 43–390/week
- `engagementRate`: 0.26–0.53 (26%–53%)
- `bounceRate`: 0.47–0.74 (inverse of engagementRate, as expected)
- `averageSessionDuration`: ~41–169 seconds
- `screenPageViews`: 80–529/week

Full week-by-week numbers are in the raw pull (not reproduced row-by-row here) — no gaps, no zero weeks. A visible spike in weeks `202623`/`202624` (345/297 users) — worth checking against the church calendar for a known event.

### Channel group (`sessionDefaultChannelGroup`), last 90 days

| Channel | Sessions | Users | Engagement rate |
|---|---|---|---|
| Direct | 1,907 | 1,379 | 32% |
| Organic Search | 436 | 247 | 67% |
| Referral | 383 | 353 | 15% |
| Organic Social | 50 | 38 | 64% |
| Unassigned | 11 | 11 | 0% |
| AI Assistant | 3 | 2 | 33% |

Direct dominates volume but has middling engagement; Organic Search and Organic Social are lower-volume but meaningfully higher engagement. "Referral" has by far the lowest engagement rate (15%) despite decent volume — worth a closer look at what's actually referring (bot traffic is a real candidate, see below).

### Device category, last 90 days

Mobile leads (1,656 sessions, 38% engagement), desktop second (1,063 sessions, 30% engagement, notably higher 70% bounce rate), tablet and smart TV are negligible volume.

### Top pages, last 90 days (185 distinct paths, top 15 shown)

`/` (home, 1,214 views) and `/fbc` (438 views, 83% bounce rate — much higher than site average, worth checking what that page is / whether it's landing traffic that doesn't belong there) lead by a wide margin. Several top-15 entries are **opaque CMS-generated UUID paths** (e.g. `/page/bee259d9-a524-11f0-a50b-02d16a621067`) with no human-readable slug — GA4 alone can't tell you what content that is; would need a cross-reference against the site's CMS/page list to make these reportable.

### Geography — city/country, last 90 days (⚠️ likely bot contamination)

Country breakdown: United States (1,431 users) is largest as expected, but **Singapore is the #2 country with 460 users** — almost matching the entire rest of the world combined outside the US. City breakdown confirms this: **Singapore is the single top city (459 users, 458 sessions)**, ahead of every actual Delaware-area city (Wilmington 120, Newark 87, Pike Creek 55, Christiana 51, New Castle 48, Bear 39, Glasgow 38, Hockessin 33). Smaller but still odd counts appear for China, Iran, Nigeria. `(not set)` city is the #2 row overall (314 users) — also worth investigating (VPN/bot traffic commonly reports no city).

This pattern (a single foreign city rivaling total US traffic, `(not set)` at high volume, low-engagement "Referral" channel) is a classic bot/scraper signature, not real congregant engagement. **Any "how are we doing" report needs to either filter this out or flag it explicitly** — as pulled, raw GA4 totals materially overstate real audience reach and understate true engagement rate (bot sessions typically bounce immediately, dragging bounceRate up and engagementRate down for whichever channel they land through).

### Key events / conversions

Queried `keyEvents` by `eventName` — **all zero**. No key events (conversions) are configured on this property at all. The only events flowing are GA4's auto-collected defaults: `page_view` (4,169), `session_start` (2,786), `first_visit` (1,874), `user_engagement` (1,625), `scroll` (1,444), `click` (261), `file_download` (26), `view_search_results` (4), `form_start` (3).

Notably: **`form_start` fires only 3 times in 90 days** with no corresponding `form_submit`-style event visible in the top event list — if the site has a connect-card or contact form, GA4 currently can't tell you how many people actually completed it, only that 3 people started filling one out.

---

## Part 3 — Possible reporting angles

**Trends over time.** Weekly `totalUsers`/`sessions`/`engagementRate` line charts are immediately viable — 14 clean weeks of real data, no gaps. Worth overlaying against the church calendar (services, events) to explain spikes like weeks `202623`–`202624`.

**Sheet vs. GA4 reconciliation — likely to disagree, and that's informative.** The Sheet's "Active Web Users" / "New Web Users" / "Avg Engagement Time" / "Event Count" rows and GA4's live `totalUsers`/`newUsers`/`averageSessionDuration`/`eventCount` are almost certainly the same underlying metrics, hand-copied into the Sheet at some point in the past by whoever maintains it. A report that pulls both **should show them side by side rather than silently pick one** — a material mismatch (e.g. from GA4 data retention/rollup changing historic numbers, or bot traffic contaminating one pull but not the other) is itself a useful finding, not just noise.

**Bot-traffic filtering is close to a prerequisite, not a nice-to-have.** Given Singapore/`(not set)`/Iran/Nigeria/China showing up at meaningful volume for a single-campus Delaware church site, any "real audience" report should either apply a GA4 segment/filter (e.g. restrict to US, or exclude sessions with 0 engagement time) or explicitly present both raw and filtered numbers. Otherwise "how are we doing" numbers will be inflated by an unknown, possibly large, non-human fraction.

**Acquisition channel + engagement, cross-cut.** Channel group data already shows Direct dominates volume while Organic Search/Social punch above their weight on engagement — a "where should we invest" angle (e.g. "social posts bring fewer but more engaged visitors") falls directly out of what's already pulled.

**Page-level follow-through.** `/fbc`'s 83% bounce rate next to the homepic's cleaner numbers is a concrete, actionable page-health finding, but several other top pages are unlabeled CMS UUIDs — a real report needs a page-path → human title mapping (either from the CMS or a GA4 `pageTitle` dimension pull) before page-level findings are presentable to Bill.

**Funnel/goal visibility is currently a gap.** With `keyEvents` totally unconfigured, there's no way to see "connect card submitted," "sermon watched," or similar completion events from GA4 today — only page views and generic engagement. `form_start` (3 events) with no matching completion signal is a concrete example of this gap. If a real "how are we doing" report should answer "are visitors converting to action," that requires either GA4 key events to be configured on the property (outside Watson's control — a GA4 admin/UI change) or falling back to the Sheet's manually-tracked Connect Cards/Registration numbers as the completion proxy instead.

**Missing from both sources, worth flagging to Bill:** neither source currently ties web engagement to *individual* people/members (both are anonymous aggregate stats) — so this data can inform campus-level "is the website working" reporting, but can't answer person-level questions like "did this connect card come from someone who visited the site first." That would need session-level attribution Watson doesn't have access to via either source as configured today.

---

*Generated 2026-08-20 by Watson dev-dispatch job, read-only recon. Raw JSON pulls not committed (throwaway, job-scratch only) — this markdown file is the durable record.*
