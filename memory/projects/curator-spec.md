# Curator — Current State + Fast-First-Result Spec

*Compiled 2026-07-22 from direct code inspection of `~/watson/jobs/curator/*.py`, `~/curator/src/**`, `watson.db` (`bug_tracker`, `project_backlog`), and live crontab. No code was changed to produce this document — spec only.*

---

# Part 1 — Current State

## 0. Scope note on "a search"

Curator has no expensive *read* path — `GET /api/curator/books` (the Library/Pending list) is a single synchronous SQLite query against `curator.db`, sub-second. The only operation that could plausibly take 122s is **submitting a book** (the "Add a Book" flow: Title/Author, Link, or Cover Photo tabs), which triggers Watson's research pipeline. The rest of this document treats "a search" as that submission flow, since it's the only candidate for a two-minute duration. The frontend (`~/curator/src/app/page.tsx`) literally displays a live "Researching… (Ns)" counter, polling every 2.5s — that counter is almost certainly where "122s" was read off.

Two entry points feed the identical backend pipeline:
- Web: `~/curator` (Next.js, Vercel) → `POST /api/ingest` → Watson `POST /api/curator/ingest`
- Telegram: `curator: <title> by <author>` / `curator: <link>` / photo with `curator:` caption → `bot.py` → same `jobs/curator/worker.enqueue_job()`

## 1. Full request pipeline, in order

**Layer 1 — Vercel (Next.js, `~/curator`)**
1. Browser POSTs to `~/curator`'s own `/api/ingest` route (`src/app/api/ingest/route.ts`).
2. Route checks the session cookie, then proxies to Watson via `watsonFetch()` (`src/lib/watson.ts`) — a plain server-side `fetch()` to `${WATSON_API_URL}/api/curator/ingest` with an `X-Watson-Key` header. `maxDuration = 10` is set here specifically because Vercel Hobby hard-caps functions at 10s — this route must return fast (commit `04d27e8`).

**Layer 2 — Watson Flask (`jobs/curator/api.py`, `watson-dashboard.service`, port 5200, reached over Tailscale Funnel)**
3. `POST /api/curator/ingest` (`api.py:ingest()`) parses the payload (text/link, or multipart image), calls `jobs.curator.worker.enqueue_job()` — a single `INSERT INTO ingest_jobs (...status='queued'...)` — and returns **HTTP 202 with a `job_id` immediately**. This route never touches the research pipeline; it's a pure enqueue. (This is the fix in `04d27e8`/`d44619b` for the Vercel 10s timeout — the route used to block on the full research pass.)
4. Frontend starts polling `GET /api/ingest/status/{jobId}` → Watson `GET /api/curator/ingest/status/<job_id>` every 2.5s (`POLL_MS` in `page.tsx`), and increments its own 1s "elapsed" counter independently of the poll.

**Layer 3 — background worker (`jobs/curator/worker.py`)**
A single daemon thread (`start_worker()`, started once at Flask app boot, name `curator-ingest-worker`) polls `ingest_jobs` every 1.5s (`_POLL_INTERVAL`) for the oldest `status='queued'` row, claims it (`status='running'`), and processes it. **Deliberately single-threaded, one job at a time** — the docstring is explicit that this is because Ollama on this box already serializes requests regardless of worker concurrency, so a second worker thread would add complexity without adding throughput.

5. `_process_job()` → `_process_single()` → `jobs.curator.ingest.ingest_submission()` — this is where all the real latency lives.

**Layer 4 — `jobs/curator/ingest.py :: ingest_submission()`**
6. If image: OCR the cover via Ollama vision model (`_ocr_cover`, model `qwen2.5vl`) — *skipped entirely for a Title/Author or Link submission*.
7. If link and no title: fetch OG metadata, then an Ollama pass (`_extract_book_from_text`, model `qwen2.5:7b`) pulls a candidate title/author out of the caption — *skipped for Title/Author submissions*.
8. `title = title_case(title)` — pure string logic, no I/O.
9. `research = research_book(title, author)` — **the dominant stage, detailed in §2 below.**
10. Backfill `series`/`author` from research output if the user didn't supply them.
11. Gate: if `research["findings"]` is non-empty → `status='pending'`; if empty → `status='needs_review'` (this gate was changed 2026-07-22, commit `8aa8103`, to depend on whether any real source excerpt was found at all, **not** on `judge_spice_rating()`'s confidence — Mel sees raw findings and judges herself).
12. `_create_book()` — one `INSERT INTO books`.
13. `_add_source()` for the original submission (screenshot/link/text) plus one `_add_source()` per research source (Amazon, Goodreads, each trusted spice source) — N sequential `INSERT INTO book_sources` calls.
14. `_add_spice_findings()` — one `executemany` INSERT of the findings rows.
15. `_notify()` — **no-op as of 2026-07-22** (Telegram DM removed for privacy; see §7).

**Back in the worker (step 5 continues):**
16. `UPDATE ingest_jobs SET status='done', book_id=...`.

**Frontend, on its next 2.5s poll:**
17. Sees `status: 'done'`, stops polling, renders the result card with cover/findings/KU badge.

## 2. `research_book()` — the actual work, in call order

Everything below runs **synchronously and sequentially on the single worker thread** — there is no `asyncio`, no thread pool, no `concurrent.futures` anywhere in `jobs/curator/research.py` or `ingest.py`. Total latency is a strict *sum* of every call below, not a max.

| # | Call | What | Network target | Timeout ceiling |
|---|------|------|------|------|
| 1 | `search_top_content_sites()` | 4 separate `site:` Serper queries — one per trusted domain (commonsensemedia.org, romance.io, spicybooks.org, thefaeshelf.com), run one after another *by design* (a combined OR-query was found 2026-07-21 to let one domain's SEO duplicates crowd another out of the shared result cap) | Serper.dev | 10s × 4 |
| 2 | `fetch_full_text()` × up to 4 | One page fetch per trusted-source category actually found | CSM/SpicyBooks/FaeShelf: direct `requests.get`. **romance.io: routed through local FlareSolverr** (`localhost:8191`, headless-browser Cloudflare-challenge solver) | 10s × up to 3, **+ up to 70s for romance.io** (`maxTimeout=60000ms` + 10s buffer) |
| 3 | `search_for_author()` | Extra Goodreads-restricted Serper query — **only runs if no author was supplied** (the common case for a quick title-only add) | Serper.dev | 10s |
| 4 | `find_amazon_listing()` | Serper query for the Amazon listing | Serper.dev | 10s |
| 5 | `find_goodreads_book_page()` | Serper query for the canonical Goodreads page | Serper.dev | 10s |
| 6 | `fetch_open_library_description()` | 2 sequential calls: `search.json` then `/works/<id>.json` | openlibrary.org | 10s × 2 |
| 7 | `fetch_page_details()` × up to 2 | Amazon then Goodreads — page count, KU badge regex, cover, og:description, series | amazon.com, goodreads.com | 10s × up to 2 |
| 8 | `judge_spice_rating()` | Ollama call weighing the findings into a 0–5 rating — **skipped if `_try_reconcile_csm_spicybooks()` resolves it in-process first** (only when findings are exactly 1 CSM + 1 SpicyBooks within a small point-estimate gap) | `localhost:11434`, model `qwen2.5:7b`, `temperature=0` | 90s (`call_ollama` default) |
| 9 | `fetch_open_library_description()` retry | Only if author was `None` going in and got backfilled from search-result titles in step 3/1 | openlibrary.org | 10s × 2 |

`extract_author_from_titles()` (in-process regex over Serper result titles, requires the same author name in 2+ independent results) and all excerpt extraction (`_extract_*`) are pure string/regex work — negligible.

## 3. Timing breakdown — where the 122s likely goes

**No timing instrumentation exists.** Grepped `jobs/curator/*.py` for `time.time()`, `perf_counter`, or any duration logging — none found. Nothing in the current code records or logs how long any individual stage took. The estimate below is derived from the call graph in §2 plus documented, measured Ollama latencies elsewhere in this exact system (open bugs #21–#23, same CPU-only Beelink Ollama install) — it is **not a measurement of this specific 122s run**.

Plausible allocation for a Title/Author submission with no author supplied (the common case):

| Stage group | Realistic range | Why |
|---|---|---|
| 7 sequential Serper calls (site-search ×4, author-backfill, amazon, goodreads) | ~10–25s | Serper is normally sub-2s/call, but 7 of them run back-to-back with no overlap |
| 3 plain page fetches (CSM/SpicyBooks/FaeShelf) | ~3–15s | Fast when the page loads cleanly, up to 30s if any of the three time out at their 10s ceiling |
| romance.io via FlareSolverr | ~5–70s | Actually solving a Cloudflare JS challenge through a headless browser is inherently variable; ceiling is 70s |
| Open Library description (×2, possibly ×4 with the retry) | ~2–8s | Real public API, generally fast |
| Amazon + Goodreads `fetch_page_details` | ~2–10s | Amazon frequently returns its bot-block interstitial quickly rather than timing out |
| `judge_spice_rating()` Ollama call | **~10–120s+** | See below — this is the single most variable stage |

**The Ollama call is the most likely dominant single contributor.** This isn't speculation about Curator specifically — it's the documented, measured behavior of *this exact Beelink Ollama install* under real production load, logged in open bugs:
- Bug #21: same-hardware single-call latencies measured at **154.7s** and **110.5s** for prompts of comparable or smaller size than `judge_spice_rating()`'s findings-weighing prompt, with a documented pattern of latency "snapping back to normal" unpredictably.
- Bug #22 (still open): `OLLAMA_NUM_PARALLEL=1` means **every Ollama request on the Beelink is serialized system-wide**, regardless of `OLLAMA_MAX_LOADED_MODELS`. If a Telegram intent-classification (`gemma3:4b`), a Dev Loop request (`qwen2.5-coder:7b`), or another background job (`qwen2.5:7b`) is in flight when Curator's research pass reaches this stage, Curator's call queues behind it — invisible to Curator's own code, since `call_ollama()` just blocks on the HTTP POST.

Given (a) zero concurrency anywhere in the pipeline, (b) a FlareSolverr fetch with a 70s ceiling, and (c) an Ollama call on hardware independently measured to occasionally run 100–155s for comparable prompts, a 122s total is fully consistent with the architecture even with no single catastrophic failure — it's the sum of a normally-fast ~20–40s of HTTP/Serper calls plus one slow-but-within-normal-range Ollama call, or plus a slower-than-usual FlareSolverr solve. **Which one actually dominated on the run that measured 122s cannot be determined from current logs** — nothing in `jobs/curator/*.py` timestamps stage boundaries today.

## 4. Every source queried — sync vs. async

All sync (blocking `requests` calls or Ollama HTTP calls), run one after another on the single worker thread. Nothing here is concurrent; the *only* asynchronous element in the whole system is at the job-queue boundary (steps 3–4 in §1) — the enqueue returns immediately and the actual work happens later, in the background thread.

| Source | Purpose | Access method |
|---|---|---|
| Serper.dev | All web search (trusted-source discovery, author backfill, Amazon listing, Goodreads page) | Direct `requests.post`, sync |
| commonsensemedia.org | Spice-content prose review | Direct `requests.get`, sync |
| spicybooks.org | Spice-content structured rating | Direct `requests.get`, sync |
| thefaeshelf.com | Spice-content structured rating + content-warnings prose | Direct `requests.get`, sync |
| **romance.io** | Spice-content structured rating | **Routed through local FlareSolverr container** (`localhost:8191`, Docker, `--restart unless-stopped`) to solve its Cloudflare JS challenge — reactivated 2026-07-22 after being dormant since 2026-07-21 | Sync HTTP POST to FlareSolverr, which itself does the headless-browser fetch |
| amazon.com | Page count, KU badge, cover, description, series (og: tags + regex) | Direct `requests.get`, sync — frequently returns a bot-block interstitial instead of the real page |
| goodreads.com | Fallback for whatever Amazon didn't have | Direct `requests.get`, sync |
| openlibrary.org | Plot synopsis (tried first, ahead of Goodreads' often-truncated og:description) | Direct `requests.get` against the real public API, sync |
| Ollama (`localhost:11434`) | Spice-rating judgment, book extraction from captions, cover OCR | Direct `requests.post`, sync |

## 5. How spice-content data is extracted per book

Findings are **extracted, never synthesized** — no LLM paraphrasing touches the wording shown to the user; every excerpt is the source's own text.

- **CSM (commonsensemedia.org)**: writes prose organized under fixed section headers. Extraction locates every occurrence of `"Sex, Romance & Nudity"` on the page (it repeats 2–3×: teaser, full writeup, CTA), slices to the next known header, and keeps the *longest* occurrence (the detailed writeup), trimming CSM's own "flag iffy content" CTA boilerplate.
- **romance.io / SpicyBooks**: publish their own numeric 0–5 spice scale directly. A regex (`_ROMANCE_IO_PATTERN` / `_SPICYBOOKS_PATTERN`) tries to pull `"N/5 - Label"` structured out of the page first; falls back to a keyword-window excerpt only if the pattern isn't found.
- **The Fae Shelf**: prints its rating as repeated 🌶️ chili emoji (counted directly, not label-lookup) plus a separate, always-present "Content Warnings" prose section (blends sex/violence/thematic warnings in one paragraph, not sub-headed). Both are combined, joined by an em dash, when present.
- **Fallback for everything else**: `_extract_relevant_excerpt()` — a verbatim ±window around the first hit of any of ~18 hardcoded spice-related keywords (`spice rating`, `fade to black`, `closed door`, etc.), no LLM involved.
- **The one place judgment enters**: `judge_spice_rating()` — an Ollama (`qwen2.5:7b`, `temperature=0`) pass that weighs the extracted findings into a single 0–5 number. Refuses (`confident=False`) below `_MIN_FINDINGS=1` real finding, or on genuine cross-source disagreement (CSM-vs-SpicyBooks agreement is checked deterministically first via point-estimate mapping tables before ever calling the LLM). **As of 2026-07-22 this number no longer gates what Mel sees** — visibility is gated purely on whether any finding exists at all (§1 step 11); the computed rating is still stored but is informational only.
- Sources are capped at `_MAX_DISPLAYED_FINDINGS = 4`, ranked by a fixed priority order (CSM → romance.io → SpicyBooks → Fae Shelf).

## 6. How Kindle Unlimited is checked

- **On ingest**: `fetch_page_details()` scrapes the Amazon listing HTML for a case-insensitive `"kindle unlimited"` regex match. **Three-state, not boolean**: `True` (badge found), `False` (a real product page was fetched and confirmed no badge), or `None` (couldn't verify — most commonly because Amazon served its bot-block interstitial instead of the real page, detected via `_is_amazon_block_page()`: a fixed marker string or a page under 10KB). This distinction was added specifically to stop "blocked" and "confirmed not on KU" from silently collapsing into the same `False` value (schema migration `532b53d`/`c97d08a`, 2026-07-22).
- **Weekly refresh**: `jobs/curator/refresh_ku.py`, cron **Sunday 5am** (confirmed live in crontab). Re-checks every book where `kindle_unlimited = 1` and not rejected, using the stored Amazon source URL (or re-searching for one if missing). Only ever flips `1 → 0`, and only on an explicit `False` result — a blocked/unfetchable page (`None`) is skipped, not treated as "no longer on KU." No Telegram alert on flip — "low stakes" per the file's own docstring.
- **Manual add** (title/author with no research) never attempts a real check — leaves `kindle_unlimited` as `NULL` unless the caller explicitly supplies a value.

## 7. Caching, indexing, persisted state

- **No caching of any kind** in the research pipeline — no memoization of Serper results, no dedup check against previously-researched titles, no HTTP response cache. A duplicate submission of the same book re-runs the entire pipeline in §2 from scratch.
- **Persisted state**: `curator.db` (SQLite, separate file from `watson.db`/`congregation.db`), path `~/watson/data/curator.db`. Tables: `users`, `books`, `book_sources`, `reading_status`, `ingest_batches`, `ingest_jobs`, `spice_findings`.
- **Indexes** (plain SQLite B-tree, not a cache layer): `idx_books_status`, `idx_books_spice`, `idx_book_sources_book`, `idx_reading_status_user`, `idx_reading_status_book`, `idx_ingest_jobs_status`, `idx_ingest_jobs_batch`, `idx_spice_findings_book`.
- **No vector index, no ChromaDB involvement** — Curator is entirely separate from Watson's KB/ChromaDB system.
- Notable schema history: `kindle_unlimited` was originally `INTEGER NOT NULL DEFAULT 0`; migrated to nullable via a create-copy-drop-rename (`_migrate_kindle_unlimited_nullable`) to support the three-state KU model in §6. A bug in that migration (dangling FK references left pointing at the renamed-away temp table) caused every ingest to 500 until fixed same-day — see §8, bug #43.

## 8. LLM calls in the critical path

| Call site | Model | When it fires | Timeout |
|---|---|---|---|
| `judge_spice_rating()` | `qwen2.5:7b` | Every research pass with ≥1 finding, unless the CSM/SpicyBooks deterministic reconciliation short-circuits it | 90s (default) |
| `_extract_book_from_text()` | `qwen2.5:7b` | Link submissions where no title was supplied | 90s (default) |
| `extract_multiple_books_from_text()` | `qwen2.5:7b` | Batch "reel link" submissions (a single social link that may name several books) | 90s (default) |
| `_ocr_cover()` | `qwen2.5vl` (vision) | Cover-photo submissions | 180s explicit — set high because `qwen2.5vl` (8.3B) measured a **137s cold-load** when evicted under the Beelink's `OLLAMA_MAX_LOADED_MODELS=3` cap, competing with `research.py`'s own `qwen2.5:7b` for a slot |

All Ollama calls hit `localhost:11434` directly — no cloud/Claude API calls anywhere in Curator (consistent with WATSON_ARCHITECTURE.md's "no Claude API calls in automated Watson jobs" rule). Curator's model choice (`qwen2.5:7b`) is not yet listed in WATSON_ARCHITECTURE.md's LLM Stack table — the table lists that model for congregation/email/State-of-Church jobs but doesn't currently mention Curator as a consumer, which is a documentation gap rather than a functional one.

## 9. Known open bugs / backlog touching Curator

**Curator-specific, `bug_tracker`:**
- **#43** (resolved, `532b53d`, 2026-07-22): dangling FK references after the KU-nullable migration renamed `books` away and back — every `INSERT` into `ingest_jobs`/`book_sources`/`reading_status`/`spice_findings` failed with `no such table: main.books_old_ku_migration`, surfacing to users as "Couldn't reach Curator." Root cause was the base schema still declaring `kindle_unlimited NOT NULL`, so the buggy migration would re-fire on any fresh DB — fixed by making the base schema nullable so the migration is now a permanent no-op.

**Curator-specific, `project_backlog`:**
- **#18** (done, 2026-07-22): FlareSolverr container — the general-purpose Cloudflare-bypass infrastructure that romance.io now depends on. Framed as general-purpose (any future Cloudflare-blocked research source), with romance.io as the concrete driver.

**Not Curator-specific but directly relevant to its latency, all still open:**
- **#21**: Ollama on this Beelink shows real, self-resolving latency spikes on CPU-only inference — measured 110.5s and 154.7s single-call durations on comparable/smaller prompts than Curator's own `judge_spice_rating()` call, not conclusively root-caused.
- **#22**: `OLLAMA_NUM_PARALLEL=1` still serializes every Ollama request on the box regardless of how many models are resident (`MAX_LOADED_MODELS` was raised 1→3 on 2026-07-17, which only fixed the evict/reload-thrash portion, not request-level contention). Any concurrent Telegram/Dev Loop/background-job Ollama call queues in front of Curator's.
- **#23**: standing caution against ever routing `qwen2.5:14b` back onto the Beelink without first testing it under real concurrent load — not applicable to Curator directly (Curator uses `qwen2.5:7b`/`qwen2.5vl`), but same root-cause family as #21/#22.

No other open bug or backlog row (searched both tables for `curator`, `%urator%` in every text column) references Curator by name.

---

# Part 2 — Fast-First-Result Spec

*Target: attributed spice-content findings visible to Mel/daughters within 15s of submission. Spec only — no code changes. Builds directly on Part 1.*

## 0. Framing: why "under 15s" forces a tiered architecture

Two components in the current critical path cannot be reliably bounded under 15s without either sacrificing the thing prioritized highest (spice accuracy) or violating a standing constraint:

- **`judge_spice_rating()`** (Ollama, `qwen2.5:7b`) — real measured latencies on this exact Beelink hardware run 90–155s (bug #21). Forcing it under 15s means a smaller/faster model or GPU offload — the former trades accuracy (priority #1, off the table), the latter means routing to FMSPC, which is barred from the automated job loop as a **permanent standing decision** (not proposing to revisit that here).
- **romance.io via FlareSolverr** — a live Cloudflare-challenge solve, ceiling 70s, inherently variable.

So "under 15s" can't mean "the whole pipeline finishes in 15s." It has to mean **the first delivered result is real and useful within 15s, and the rest arrives later without blocking that**. That reframing is what items 3–5 below implement, and it's why tiered/progressive delivery isn't one option among six here — it's the precondition that makes the other five actually add up to a 15s target instead of a 40s or 80s one.

Convenient framing precedent already exists in the code: commit `8aa8103` (2026-07-22) already stopped gating visibility on the computed rating — "the goal is getting books in front of Mel with real excerpts... not a computed number she has to trust blind." Deferring the *number* to a later stage while delivering the *excerpts* fast is a direct extension of a decision Bill already made, not a new tradeoff.

## 1. Parallelize all independent network I/O within each stage
**Addresses: "parallelizing source queries"**

Today every Serper query, every page fetch, and every Open Library/Amazon/Goodreads call runs strictly sequentially on one thread — total latency is a sum, not a max. None of these calls depend on each other except in two places: (a) a source's URL must be known before its page can be fetched, and (b) `judge_spice_rating()` needs the findings. Everything else is independent and safe to fire concurrently (e.g. via a bounded thread pool — these are blocking `requests` calls, not `asyncio`, so threading is the natural fit without a rewrite).

Restructure into two parallel waves:
- **Wave 1** (parallel): all Serper queries — trusted-source site-searches, author-backfill, Amazon-listing, Goodreads-page.
- **Wave 2** (parallel, once Wave 1's URLs are known): trusted-source page fetches (CSM/SpicyBooks/Fae Shelf), Amazon `fetch_page_details`, Goodreads `fetch_page_details`, Open Library description.

**Estimated impact: Large.** Converts ~13 sequential calls (each with meaningful individual latency even when fast) into two waves bounded by their *slowest member*, not their sum. Typical case: sequential ~15–30s → parallel ~3–6s. Worst case (multiple sources near their timeout): sequential ~60–70s → parallel bounded by the tightened per-source timeout (see #2), ~5–8s. This is the single largest lever available short of removing components entirely.

## 2. Tighten and enforce per-source timeouts, with graceful skip
**Addresses: "per-source timeouts with graceful skip"**

The current uniform `timeout=10` per call already means a slow source doesn't hang forever, but combined with sequential execution a single slow/unresponsive source still costs its full 10s before the next call even starts. Once parallelized (#1), the *ceiling* on each wave becomes whatever the slowest member's timeout is — so the timeout value itself becomes the real lever, not just a safety net.

- Reduce trusted-source page fetches (CSM/SpicyBooks/Fae Shelf) and Serper calls to ~4–5s each — all of these are normally sub-2s in practice; a 4–5s ceiling still gives 2–3x normal headroom without being a bottleneck.
- Amazon/Goodreads `fetch_page_details`: ~4–5s — Amazon's bot-block page returns fast even when it blocks, so this isn't the risky one.
- Open Library: ~4–5s — real public API, generally fast.
- A source that misses its window is dropped from this book's *first* result (existing `if not text: continue` skip logic already does this for outright failures — extend the same pattern to enforced short timeouts) and gets one retry attempt in Stage B (#3) rather than being lost entirely.

**Estimated impact: Large, mainly in combination with #1.** Alone, tightened timeouts save little (most calls already finish well under 10s). Combined with parallelization, this is what converts the *worst-case* wave latency from ~10s (current per-call ceiling) to ~4–5s, and is what makes the 15s target robust against one flaky source rather than only fast in the typical case.

## 3. Split the pipeline into Stage A (synchronous, ≤15s target) and Stage B (background enrichment, no deadline)
**Addresses: "tiered/progressive response delivery" — the load-bearing change**

Redefine what "first result" means. Instead of one atomic `ingest_submission()` call that either fully completes or fails, split into two phases against the same `ingest_jobs`/`books` rows:

**Stage A** — runs immediately, target ≤15s, everything parallelized (#1) and time-boxed (#2):
- Wave 1 + Wave 2 above, **excluding romance.io/FlareSolverr** (#4) and **excluding the Ollama rating call** (#5).
- Persists a book row as soon as Stage A completes: title/author, cover/description/series if they arrived in time, KU if Amazon responded in time, and — critically — **whatever spice_findings excerpts were gathered from CSM/SpicyBooks/Fae Shelf**, unrated.
- Marks the job/book as "partial" rather than "done."

**Stage B** — runs after Stage A returns, no deadline, same background worker:
- romance.io via FlareSolverr (#4).
- `judge_spice_rating()` (#5).
- One retry for any Stage A source that missed its timeout window.
- Updates the same book row in place; flips status to fully "done" once the Ollama judgment resolves (or times out for real, at its existing 90s ceiling).

**Priority mapping, explicit per the given ordering:**

| Priority | Data | Stage | Rationale |
|---|---|---|---|
| 1 — spice accuracy | CSM / SpicyBooks / Fae Shelf excerpts (verbatim, attributed) | **A** | This *is* the accuracy-bearing content — the excerpts, not the number |
| 1 — spice accuracy (secondary) | romance.io excerpt, numeric `spice_rating` | B | Corroborating source + synthesized number — useful but not what "accuracy" hinges on per the 2026-07-22 gating decision |
| 2 — KU | Amazon KU badge | **A, best-effort** (short timeout, no hard dependency) | Fast when Amazon isn't blocking; falls to B on timeout/block rather than holding up delivery |
| lower — everything else | cover, description, series, page count, Goodreads fallback | **A opportunistically, B otherwise** | Cheap enough to ride along in the same parallel waves, but explicitly allowed to arrive late or never per your instruction |

**Companion changes this requires (naming, not designing):** a new intermediate `ingest_jobs`/`books` status (e.g. `partial`) between `running` and `done`; the frontend's binary "Researching… / done / failed" state needs a third "showing partial results, still enriching" render state, since it already polls every 2.5s and just needs to render on a status it currently doesn't have. The Telegram `curator:` path needs **no change** — `_notify()` is already a no-op and that path already tells the user to "check Pending in a bit" rather than waiting live, so it's already compatible with results arriving in two waves.

**Estimated impact:** This is what makes ≤15s achievable *at all* for the highest-priority content, given #4/#5 below cannot be forced under 15s without breaking priority #1. Without this split, hitting 15s would require accepting a materially worse spice-content judgment (smaller/faster model) or breaking the FMSPC exclusion — both worse than deferring lower-priority data.

## 4. Remove FlareSolverr/romance.io from the synchronous path entirely
**Addresses: "whether FlareSolverr/headless-browser scraping can be moved out of the live request path entirely" — yes, and it should be**

Evaluated three options:

- **(a) Move to Stage B unconditionally.** romance.io becomes background-only enrichment; its finding (if/when it resolves) is appended to `spice_findings` after Stage A has already shown a result. **Recommended.** This is the only option that makes the 15s guarantee *unconditional* rather than "usually fast." Given `_MIN_FINDINGS = 1` and three other trusted sources already sufficient to gate a result today, dropping romance.io from the fast tier costs nothing in first-delivery accuracy — it was always a corroborating source, never the sole one.
- **(b) Pre-fetch for "likely" titles out-of-band.** Rejected — see #9, no reliable signal exists for what to pre-fetch.
- **(c) FlareSolverr session/cookie reuse** so only the *first* request per session pays the full Cloudflare-solve cost. Worth doing **opportunistically as a Stage B optimization** (shrinks typical background completion time, doesn't affect the user-facing guarantee either way), but not reliable enough (Cloudflare session lifetimes vary) to load-bear the 15s target even if it were still in Stage A.

**Estimated impact: Removes up to 70s of worst-case latency from the guaranteed-fast path, unconditionally.** Combined with #1, this is the second-highest-leverage change alongside parallelization — arguably higher, since #1 reduces a sum-of-many-small-timeouts problem while this eliminates the single largest and least predictable one outright.

## 5. Defer the Ollama spice-rating judgment (`judge_spice_rating`) to Stage B
**Addresses part of "spice content accuracy first" via the tiered-delivery mechanism (#3), called out separately because it's the other major latency source**

Same logic as #4: real measured single-call latency on this hardware (90–155s, bug #21) cannot be forced under 15s without a worse model, and the numeric rating is explicitly the lower-trust, secondary output per the existing 2026-07-22 gating decision. Moves to Stage B; the deterministic CSM/SpicyBooks reconciliation shortcut (`_try_reconcile_csm_spicybooks`) still runs first and, when it applies, could actually resolve *inside* Stage A cheaply (it's pure in-process arithmetic, no LLM call) — worth keeping in the fast path since it's free.

**Estimated impact: Removes the single largest and most variable latency source (potentially >100s) from the synchronous path.** This is not optional if the 15s target is to hold — no realistic tightening of Ollama's timeout gets a CPU-only 7B-model judgment call reliably under 15s on this hardware.

## 6. Title-level dedup cache — skip re-research for already-curated titles
**Addresses: "caching/pre-indexing previously-curated titles"**

Before Stage A starts, check `books` for a normalized (title, author) match with `status != 'rejected'`. On a hit: skip research entirely, return the existing book (attaching a `reading_status` row for the submitting user if relevant) in well under 1s. Add a staleness window (e.g., re-research if the existing row is >90 days old) so genuinely stale entries don't calcify — mirrors the existing weekly-refresh pattern already used for KU.

**Estimated impact: High but narrow.** 100% latency elimination (<1s) for *repeat* submissions — plausible in practice since Curator has multiple household users who may independently submit the same trending title — but **zero impact on a genuinely novel title's first search**, which is the harder case the 15s target is really about. This is a real win, not the primary lever.

## 7. (Lower priority) Short-TTL per-URL page-fetch cache
**Addresses: "caching/pre-indexing," secondary form**

Beyond title-level dedup (#6), cache individual fetched pages (CSM/SpicyBooks/Fae Shelf/Amazon/Goodreads HTML, keyed by URL) for ~24h. Helps when: the same book is searched under slightly different title phrasing by two different users, or a Stage B retry re-fetches a page Stage A already pulled moments earlier.

**Estimated impact: Small-moderate, mainly a load-reduction/politeness win** (fewer redundant hits to CSM/SpicyBooks/target sites) rather than a latency win for the primary "first novel search" case. Fits the "everything else, drop if it's a bottleneck" bucket — implement after the above, not before.

## 8. Keep `qwen2.5:7b` warm to avoid cold-load tax in Stage B
**Addresses incidental to "background pre-warming," but the applicable form of it**

Mirrors the existing `jobs/intent/keep_warm.py` pattern used for `gemma3:4b` (periodic ping to keep the model resident, avoiding the 22–80s cold-load numbers documented in `model_benchmark_20260715.md`). Since `judge_spice_rating()` moves to Stage B (#5), this doesn't affect the 15s guarantee, but it shortens how long Stage B enrichment takes to actually finish — relevant because Mel is still waiting on *some* horizon for the full picture, even if it's no longer blocking.

**Estimated impact: Moderate, Stage-B-only.** Could shave 20–80s off Stage B completion time when the model would otherwise have been evicted, zero impact when it's already warm from other jobs (it's already Watson's primary accuracy-sensitive-job model per the LLM Stack table, so it's likely warm fairly often already).

## 9. Predictive pre-warming of "likely searches" — evaluated and **not recommended**
**Addresses: "background pre-warming of likely searches" — explicit rejection, per the request to evaluate it**

Unlike a public search product, Curator has no reliable signal for "what will probably be searched next" — submissions are Mel/daughters manually adding specific titles they saw on TikTok/Instagram, not queries against a fixed catalog with observable trending patterns. There's no query log, no external trending feed, and no fixed book catalog to pre-index against. Building a prediction/pre-warm system here would be speculative infrastructure with no grounded signal to drive it.

**Estimated impact: None proposed — recommend dropping this from scope entirely**, in favor of #8 (the one genuinely applicable form of "warming" — keeping the *model* warm, not predicting *content*).

## 10. Frontend: render a "partial results" state
**Required companion change, not a backend item — named for completeness**

`~/curator/src/app/page.tsx` currently has three states: submitting → researching (spinner + elapsed-seconds counter) → done/failed. Needs a fourth: "partial results shown, still enriching in background" — likely reusing the existing `ResearchResultCard` with a small badge/indicator (e.g., "KU + full rating still loading…") rather than a new component, since Stage A output is structurally the same book+findings shape Stage B just adds to.

**Estimated impact: N/A (UX, not latency)** — but without this, Stage A completing fast is invisible to the user; the polling loop would need to distinguish "partial" from "done" or the whole benefit of #3 is lost.

## 11. Decouple Stage A concurrency from Stage B's Ollama serialization
**Secondary throughput note, not required for the 15s target on a single submission, but relevant for batches**

The current worker deliberately processes one `ingest_jobs` row at a time, reasoning that Ollama serializes anyway (bug #22, `OLLAMA_NUM_PARALLEL=1`) so extra worker threads wouldn't help. That reasoning is only true for the Ollama-bound portion. Once Stage A (#3) contains no Ollama calls, it's pure I/O-bound and safe to run multiple Stage A jobs concurrently (e.g., across a batch submission of 5 books) even while Stage B remains single-threaded/serialized behind Ollama's real constraint.

**Estimated impact: Doesn't affect single-book latency, but meaningfully improves batch-submission latency** — today a 5-book batch pays each book's *full* sequential pipeline one after another; with Stage A decoupled, all 5 books' Stage A work can overlap, and only the Ollama-bound Stage B portion needs to queue.

## Projected timeline (single-book, Title/Author submission, no author supplied)

| | Today (sequential, no changes) | After #1–#5 |
|---|---|---|
| First meaningful result (attributed spice excerpts visible) | ~60–150s (whole pipeline, one atomic result) | **~5–12s** (Stage A: parallel waves, tightened timeouts, no FlareSolverr/Ollama) |
| KU badge | same as above | Usually within Stage A; falls to Stage B (~+seconds to tens of seconds later) if Amazon blocks/times out |
| Numeric spice_rating + romance.io corroboration | same as above | Stage B, background — same real-world duration as today (~10–150s, Ollama-variance-bound), but **no longer blocking** |
| Duplicate/already-curated title | same as above | **<1s** (#6 dedup short-circuit) |

The 15s target is met for the priority-1 content (attributed spice excerpts from CSM/SpicyBooks/Fae Shelf) and best-effort for priority-2 (KU), by construction of the Stage A/B split — not by making the slow components faster, but by no longer requiring them to finish before something real is shown.

---

# Investigation Notes

## CSM paywall investigated 2026-07-23 — Curator's extraction unaffected

CSM's subscriber paywall is a client-side collapsed-section UI, not a server-side content gate — raw HTTP fetch (Curator's method, no cookies/session) returns the full "Sex, Romance & Nudity" write-up in the HTML regardless of login state, confirmed via Playwright comparison (rendered/visible page shows the short teaser; raw HTML contains the full paragraph either way). Verified against ACOTAR, Beach Read, Fourth Wing (fresh fetches, full explicit content extracted) and The Thirteenth Child (existing stored finding matches live content exactly).

Caveat: sample size is small (1 historical record + 3 fresh spot-checks) — most prior CSM findings were unavailable to check due to a same-session data loss. If CSM changes their site architecture to server-side truncation in the future, this conclusion would need re-verification, not assumed still valid indefinitely.
