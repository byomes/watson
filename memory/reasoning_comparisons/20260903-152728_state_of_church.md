# Reasoning Comparison — state_of_church

**Prompt source:** state_of_church (jobs/connect_cards/state_of_church.py build_report()), real congregation.db data
**Run at:** 2026-09-03T15:27:28

**Judging protocol:** fabrication check runs first, per candidate, before any quality/completeness commentary. Every factual claim in each output is cross-referenced against the source data below; any claim not directly grounded there is listed. A fabricated claim disqualifies that output outright — it is not weighed against completeness, style, or usefulness. This is not a new rule: it enforces WATSON_ARCHITECTURE.md's existing 'No hallucination. If Watson does not know, Watson says so and stops' principle as a hard first-pass gate rather than leaving it as one quality factor among several. No automated grading beyond this structural gate — a human reads and completes the fabrication check and quality notes below.

---

## System prompt
```

```

## User prompt (source data to cross-reference every claim against)
```
You are Watson, AI assistant to Dr. Bill Yomes, Senior Pastor of Catalyst Community Church in Wilmington, DE, with both a Wilmington campus and an Online campus.

Reference context — church attendance benchmarks (use this only to judge whether this week's numbers reflect normal variance or an actual trend; do not quote or repeat it verbatim):
# Watson Context Frame: Church Attendance Benchmarks
*Compiled July 16, 2026 — for `memory/projects/benchmarks.md`.*
*Purpose: give Watson's synthesis prompt (state_of_church.py) enough context to distinguish normal variance from an actual trend.*

**Last verified:** 2026-07-16
**Update policy:** `jobs/research/benchmark_check.py` runs weekly (Thu 6am), checks for new published research, and — only after Bill approves via Telegram — appends a dated entry to the Update Log below. Sections 1–3 are only rewritten manually, on Bill's call; the Update Log is where new findings land first.

---

## Update Log
*Newest entries on top. Each entry is additive — old entries are never deleted, only superseded in practice.*
- **2026-09-03** — facebook.com: Weekly church attendance in the US has stabilized at approximately 26%, according to a 2025 study by the Pew Research Center.
- **2026-09-03** — facebook.com: Around 52% of Protestant churches have experienced growth in worship service attendance over the last two years, from 2023-2025.
- **2026-09-03** — research.lifeway.com: Pastor views report that church attendance is growing, but research shows that most unchurched newcomers are still not attending church services.
- **2026-09-03** — research.lifeway.com: Slightly more than half of U.S. Protestant pastors report that their average weekly worship attendance is growing.
- **2026-09-03** — research.lifeway.com: Slightly more than half of U.S. Protestant pastors report a slight increase in average worship service attendance, with 54% saying their attendance has grown since 2023.

- **2026-07-16** — Initial compilation. See Sources.

## Entry format for future updates
`- **YYYY-MM-DD** — [source, e.g. "Lifeway Research"]: one-sentence takeaway. [Update sections 1–3 if it changes a rule of thumb above; otherwise log only.]`

---

## 1. Seasonal pattern — the thing missing from this week's read

This is the single highest-value fact to wire in. It's well-documented, consistent across sources, and directly explains most summer dips.

- Typical summer decline: 20–30% off baseline, with June running roughly a quarter down and July often the worst month — some tracking puts July as high as a third down from peak. Described as near-universal across denominations and church sizes.
- Drivers are structural, not spiritual: school being out, vacation travel, weekend sports/camps, small groups on hiatus. None of it reflects declining commitment.
- Recovery is not automatic in September. Attendance drifts back over several weeks rather than snapping back the first fall Sunday; intentional "welcome back" pushes recover faster.
- Variance within summer is itself high — offsetting effects (snowbirds/travelers returning cancel out vacationers leaving) mean week-to-week swings of 15–20% in June–August are within normal range, not a signal.

Rule for Watson: June–August attendance numbers should never be compared to spring/fall baselines without a seasonal caveat, and a single down week inside this window should not be called a trend at all.

## 2. National attendance trends (2024–2026) — the bigger picture

- The multi-decade national decline appears to be leveling off, and 2025 data showed the first uptick in median in-person attendance since tracking began in 2000 (Hartford Institute/Lifeway EPIC study, ~7,000 congregations) — median weekly attendance rose from 65 to 70.
- Pew's 2023–24 Religious Landscape Study found the long decline in Christian identity has slowed and monthly attendance is roughly flat — about a third of U.S. adults attend at least monthly, a quarter weekly.
- The recovery is uneven and concentrated in larger congregations — the share of attendees in congregations over 250 rose from 64% to 78% since 2015, partly because ~4,000 Protestant churches closed in 2024 alone, more than were planted.
- About half of congregations grew attendance at least 4% from 2022–2024, mostly people returning to in-person worship post-pandemic; a similar share stayed flat or declined.
- Gen Z and Millennial attendance is up since 2020 and now outpaces older generations in frequency.

Rule for Watson: Flat-to-slightly-declining attendance at a small/mid congregation is currently consistent with the national picture, not a red flag on its own.

## 3. Small-church-specific context (Catalyst's actual peer group)

- Congregations under 50–100 in attendance make up the majority of U.S. churches by count but a much smaller share of total worshippers, since large churches hold a disproportionate and growing share of attendees.
- Growth in this size bracket is uncommon even in healthy years — small church pastors themselves are the most skeptical group about their own church's growth trajectory.
- Implication: a small church holding steady, or fluctuating within a normal band week to week, is performing in line with its size class.

Rule for Watson: Never imply flat or single-digit-week fluctuation is underperformance relative to "churches in general" — the relevant comparison class is small congregations, where flat is normal and growth is the exception.

## 4. How Watson should apply this

1. Never call a single week's number a trend. Require 3+ consecutive weeks outside the rolling average before using language like "decline" or "downward trend."
2. Check the calendar before commenting on attendance. June–August (and major holiday weekends) get an automatic seasonal caveat.
3. Anchor comparisons to congregation size, not aggregate national averages.
4. Frame recovery language correctly — post-summer recovery is gradual, not an immediate snap-back.
5. Don't assume linear growth is the healthy baseline. Plateau is normal, not a symptom.
6. When in doubt, describe — don't diagnose. Report the numbers, note whether they fall inside/outside the normal seasonal range, and stop there.

## Sources referenced
Pew Research Center (2023–24 Religious Landscape Study, "Religion Holds Steady in America" Dec 2025); Hartford Institute for Religion Research / Lifeway Research EPIC project ("Signs of Rebound Amid Uneven Recovery," 2025–26); Lifeway Research (2024 ACP Church Performance report; "Half of Churches Experiencing Post-Pandemic Attendance Growth," 2025); Barna Group / Gloo State of the Church study (2025); Church Answers (Thom Rainer, summer slump surveys); Unstuck Group, Subsplash, Ministry Brands (summer seasonal giving/attendance analyses).


Based on this week's church data below, write exactly one cohesive 2-3 paragraph pastoral synthesis for Dr. Bill, following these rules:
a. The normal range and in/out-of-range verdict given below (COMBINED TOTAL VS NORMAL RANGE) apply ONLY to the combined total across both campuses. Never state or imply that a single campus's average is 'within' or 'outside' that combined range — campus-level averages are for descriptive context only, not range comparison. Judge overall attendance health using the combined verdict, not campus-level arithmetic.
b. Only use language like 'trend' or 'decline' if CONSECUTIVE WEEKS OUTSIDE NORMAL RANGE below is 3 or more. A number inside the normal range, or a one-week dip, is not a trend — say so plainly if that's the case.
c. If SEASONAL CAVEAT below is not 'none', lead with it before commenting on any dip — never call a summer or holiday-week dip a decline.
d. Do not imply continuous week-over-week growth is the expected baseline — a plateau is healthy, not a symptom.
e. Report the numbers plainly. Only add interpretive or diagnostic language when it's grounded in the benchmarks context above or a real sustained deviation; otherwise describe, don't diagnose.

Also comment on engagement health (what the Consistent/Active/Occasional/Lapsed distribution reveals), areas of concern, and who may need attention. Do not include a summary paragraph at the end. Do not repeat yourself. Do not include a 'Watson\'s Read:' label or any other label inside the text.

WEEK OF: August 31, 2026
ATTENDANCE: Online 11 (+0), Wilmington 92 (+39), Total 103 (+39)
4-WEEK AVG: Wilmington 71, Online 12
8-WEEK AVG: Wilmington 72, Online 12
TREND: Wilmington Stable, Online Declining
NORMAL RANGE (8-wk mean ± 1 std dev, combined): 73–95
COMBINED TOTAL VS NORMAL RANGE: 103 — OUTSIDE (range 73-95)
CONSECUTIVE WEEKS OUTSIDE NORMAL RANGE: 2
SEASONAL CAVEAT: Currently inside the June–August seasonal dip window — 20–30% below baseline is normal here, not a decline.
ENGAGEMENT: Consistent 63, Active 43, Occasional 35, Lapsed 18
SPECIAL EVENTS (past 14 days): 0 — none
FIRST-TIME VISITORS: 0
OPEN FOLLOW-UPS: 4
PRAYER REQUESTS: 18 requests from: Allyson, Cherita, Connor, Fred, Gregory, Kaci, Kalliope, Kassia, Kassia, Kayla, Kayla, Kellianne, Kevin, Lucie, Margarita, Peter, Ramona, Zion
MEMBERS NOT SEEN 14+ DAYS: 25 members: Janet, Edward, Shawn, Lynne, Brandi, Katie, Ray, Bree, Catlyn, Dan, Elizabeth, Catherine, Dan, Kimberly, John, Kenzie, Helen, Sarah, Jackie, LaToya, Marlena, Sarah, Rosalinda, Alijah, Damier; excluded from reporting: 1 disconnected, 5 non-local

You must respond in English only. Do not use any other language. Begin writing now:
```

---

## Baseline — `qwen2.5:7b` (current production model)

*54.11s total, 251 tokens, 53.7s eval, 0.17s load*

### Fabrication check (evaluated FIRST — a fabricated claim disqualifies the output outright, regardless of completeness or style, per WATSON_ARCHITECTURE.md's 'No hallucination' principle)

**Verdict: FAIL**

- **"The two disconnected members are excluded from this count."** — Source says `excluded from reporting: 1 disconnected, 5 non-local`. There is 1 disconnected member, not two. A fabricated number, not a rounding/paraphrase issue.
- **"This week's attendance figures show a positive trend..."** — Rule (b) in the prompt is explicit: *"Only use language like 'trend' or 'decline' if CONSECUTIVE WEEKS OUTSIDE NORMAL RANGE below is 3 or more."* Source says `CONSECUTIVE WEEKS OUTSIDE NORMAL RANGE: 2`. Calling this a "trend" directly contradicts an explicit constraint given in the same prompt — not a fact pulled from nowhere, but an assertion the source data explicitly forbids making. Compounded by then contradicting itself two sentences later ("this increase should not be interpreted as a true trend"), i.e. it doesn't even apply its own claim consistently.
- Misapplies the seasonal-dip caveat: the caveat exists to explain *low* attendance (`20–30% below baseline is normal here`), but this week's total (103) is *above* the normal range, not below it. Framing an above-range increase as something explained away by a "seasonal dip" is a logic error, not a fabricated fact per se — noted but not counted toward the FAIL verdict on its own.

Two of the three issues above are either an invented number or an assertion the source explicitly prohibits — enough for an outright FAIL regardless of the rest of the output's quality.

### Full output

```
This week’s attendance figures show a positive trend with the combined total of 103 individuals attending our services across both campuses, which is outside the normal range (73-95) set by historical data over eight weeks. However, given that we are currently within the expected seasonal dip for summer, this increase should not be interpreted as a true trend or decline. The Wilmington campus has maintained its stable attendance while there has been a slight decline in online participation, which is concerning but does not yet meet the criteria for a sustained pattern of decline.

Engagement among our members remains diverse with 63 consistent attendees and 43 active participants, which is encouraging. However, 18 lapsed individuals suggest we should continue to focus on reconnecting with those who have been absent. The two disconnected members are excluded from this count. No special events or first-time visitors were recorded, but there were four open follow-ups and 18 prayer requests, indicating active engagement in our community’s support network.

Overall, while the slight dip in online attendance is a concern, the increase in combined total attendance is a positive sign within the context of summer dips. We should continue to monitor trends closely but remain optimistic about our overall reach and engagement.
```

### Quality/completeness notes

N/A — disqualified by fabrication check.

---

## Candidate — `qwen3:8b` (think=False)

*243.41s total, 270 tokens, 74.12s eval, 7.75s load*

### Fabrication check (evaluated FIRST — a fabricated claim disqualifies the output outright, regardless of completeness or style, per WATSON_ARCHITECTURE.md's 'No hallucination' principle)

**Verdict: FAIL**

- **"63% of attendees classified as consistent and 43% as active"** / **"35% occasional attendees and 18% lapsed members"** — Source gives `ENGAGEMENT: Consistent 63, Active 43, Occasional 35, Lapsed 18` as raw head-counts (they sum to 159 people), not percentages. Relabeling all four counts as percentages of attendees is a fabricated statistic on every single engagement figure in the output — the real percentages would be roughly 40% / 27% / 22% / 11% of the 159 people counted, not 63% / 43% / 35% / 18%. This isn't a rounding slip, it's presenting a different kind of number than what's in the source, four times over.
- **"This places the total in the lower end of the seasonal dip window"** / **"This dip should be viewed in the context of the summer slump"** — the week's total (103) is *above* the normal range (73–95), the highest recent figure, not a dip. Describing an above-range increase as being in "the lower end" of a "dip window" is not grounded in the data — it's the opposite of what the numbers say. (Same underlying error as the baseline's misapplied seasonal caveat, arguably worse here since it's stated twice and more explicitly mischaracterizes the direction of the number.)

The percentage fabrication alone — four numbers relabeled with a unit the source never used, materially changing what they'd mean to a reader — is enough for a FAIL on its own, and the "lower end of dip window" framing compounds it.

### Full output

```
The combined total attendance for the week of August 31, 2026, stands at 103, which is outside the normal range of 73–95. This places the total in the lower end of the seasonal dip window, which is expected during June–August due to structural factors like vacation travel and summer activities. While the combined total is 39 above the previous week, the 2 consecutive weeks outside the normal range suggest a potential shift, though not yet a confirmed trend. The Wilmington campus shows stability, while the Online campus is declining. This dip should be viewed in the context of the summer slump, not as a decline in commitment.

Engagement health reveals a mix of consistent and active participants, with 63% of attendees classified as consistent and 43% as active. However, the presence of 35% occasional attendees and 18% lapsed members indicates some concern. The lack of first-time visitors and the absence of special events in the past 14 days may contribute to lower engagement. Four open follow-ups and 18 prayer requests highlight ongoing pastoral care needs, particularly for members who have not attended in over 14 days. These individuals, including Janet, Edward, and others, may benefit from targeted outreach to re-engage them.
```

### Quality/completeness notes

N/A — disqualified by fabrication check.

---

## Reviewer summary

**Both models FAIL the fabrication check on this prompt.** This does not repeat the clean qwen3:8b-wins result from the earlier `memory_consolidation` run — that was one easy, mechanical prompt; this is the actual target job type (multi-rule pastoral synthesis over real congregation data), and neither model handled it cleanly.

- `qwen2.5:7b`: one invented number ("two disconnected" vs. real 1) plus an explicit violation of a rule stated in its own prompt (calling 2-of-3-required weeks a "trend"), which it then contradicts itself on two sentences later.
- `qwen3:8b`: a more pervasive error — relabeled all four raw engagement head-counts as percentages (fabricating four numbers, not one), plus its own version of the seasonal-caveat misapplication, stated twice.

Neither should be trusted on this job type based on this single sample. If forced to rank the two failures, `qwen3:8b`'s is arguably the more structurally broken one (systematic count-to-percent fabrication across every engagement figure) versus `qwen2.5:7b`'s narrower single-number miss — but "less bad than the other failure" is not the same as passing, and this is one sample of one job type. See the second run (`skill_audit`) for a different real prompt on the same target job class before drawing a routing conclusion.
