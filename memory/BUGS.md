# Watson Bug Tracker
_Auto-generated nightly from bug_tracker. Source of truth is the database — do not hand-edit this file, changes will be overwritten._
Last generated: 2026-08-05 02:10

## Open (14)
| ID | Title | Repo | Discovered |
|---|---|---|---|
| 58 | missed_report.py cron path missing slash, silently failed weekly | watson | 2026-08-04 13:14:38 |
| 55 | watson-codeagent.service is live but broken and undocumented | watson | 2026-08-04 02:27:18 |
| 52 | jobs.skills.kb_search has no run() function but chat_stream imports it | watson | 2026-07-29 08:05:49 |
| 46 | jobs.browser: goto_safe failed for https://this-domain-does-not-exist-watson-test-12345.invalid/ | watson | 2026-07-22 14:27:51 |
| 45 | jobs.browser: goto_safe failed for https://this-domain-does-not-exist-watson-test-12345.invalid/ | watson | 2026-07-22 14:26:42 |
| 44 | jobs.browser: goto_safe failed for https://this-domain-does-not-exist-watson-test-12345.invalid/ | watson | 2026-07-22 14:25:18 |
| 40 | Backlog: dashboard chat has no durable session/history -- session_id never sent to /api/chat/stream by any caller | watson | 2026-07-18 15:34:53 |
| 26 | Telegram wrap_up() passes string session_id, causes silent hallucinated writes to memory/relational.md while reporting false success | watson | 2026-07-17 22:05:53 |
| 24 | Dashboard chat runs on Ollama by design (no ANTHROPIC_API_KEY) — stale claude-sonnet-4-6 model strings need updating if Claude is ever reactivated | watson | 2026-07-17 20:10:51 |
| 23 | qwen2.5:14b concurrent-load risk unresolved — do not route to Beelink jobs without testing classify() contention first | watson | 2026-07-17 20:02:00 |
| 22 | Ollama OLLAMA_MAX_LOADED_MODELS=1 forces single-model residency, causing classifier/general-chat model thrash | watson | 2026-07-17 17:36:22 |
| 21 | Ollama/gemma3:4b transient severe slowdown under rapid back-to-back requests (10-42s), self-resolving | watson | 2026-07-17 17:36:05 |
| 17 | Export CSV button downloads HTML instead of CSV | watson | 2026-07-15 17:22:34 |
| 10 | chat_stream() missing polish this:/kb:/shepherding: directive intercepts (present in /api/terminal, absent in /api/chat/stream — falls through to Ollama chat) | watson | 2026-07-11 16:12:30 |

## Recently Resolved (last 30 days)
| ID | Title | Repo | Resolved | Commit |
|---|---|---|---|---|
| 56 | headcount_sync.py never wired into crontab — Wilmington headcount data missing since 7/27 | watson | 2026-08-04 13:53:32 | 3b1abf94b4e2f87f0675f90be19dc965285e2688 |
| 57 | devdispatch _worktree_path() mismatches CLI worktree dirname sanitization | watson | 2026-08-04 12:36:50 | c3db18f |
| 54 | Suggest Fonts narrow stage timed out silently on every real run | watson | 2026-08-02 18:01:26 | 30161e4 |
| 53 | Dashboard Commands panel missing xkb:/debug:/bug:/run: entries | watson | 2026-07-29 08:58:46 | ad78f46 |
| 51 | Two 2026-07-27 sermon transcripts landed in kb/documents/ directly and were never indexed | watson | 2026-07-28 15:10:08 | 0a973b8 |
| 50 | email_intake.py silently dropped HTML-only email bodies (no text/plain fallback) | watson | 2026-07-27 15:49:16 | cb1399e |
| 49 | Connect Card Bcc copy would fire spurious Ollama-triage Telegram prompt to Bill | watson | 2026-07-27 15:49:16 | 5b9290c |
| 48 | All Brevo outbound email failing: missing_parameter, name is missing in to | watson | 2026-07-27 13:03:28 | 68a3dd9 |
| 47 | Curator: get_job_status() collapses unverified KU (NULL) into False | watson | 2026-07-23 03:28:44 | 8e50e7f |
| 13 | gutendex.com fully blocked by Cloudflare JS challenge (cf-mitigated: challenge) — gutenberg: search/download non-functional on both Telegram and Dashboard until resolved; confirmed browser-like User-Agent does NOT bypass it (needs JS challenge solving, a bypass library, or an alternate Gutenberg metadata source — decision deferred, see commit 21ff4c3 for the diagnostic) | watson | 2026-07-22 15:30:00 | e505d95 |
| 11 | Gutendex API (gutendex.com) now returns 403 via Cloudflare bot challenge to plain requests calls — jobs/research/gutenberg.py search()/download_and_ingest() both broken until a browser-like User-Agent or Cloudflare bypass is added; affects Telegram and Dashboard equally, dashboard routing itself verified working with mocked data | watson | 2026-07-22 15:30:00 | e505d95 |
| 43 | Curator ingest 500: dangling FK to books_old_ku_migration | watson | 2026-07-22 03:55:49 | 532b53d |
| 42 | email_intake.py re-triages same unread message every poll cycle | watson | 2026-07-21 19:30:02 | dfac939 |
| 41 | Missed-report reply mis-routed away from correction_handler.py | watson | 2026-07-20 12:54:55 | 09b41d2 |
| 39 | Bug #35's original fix never fired against the live dashboard widget -- session_id is never sent | watson | 2026-07-18 15:34:53 | 63ad893 |
| 37 | _stream_simple() now persists assistant replies to chat_messages -- closes bug #36 structurally | watson | 2026-07-18 14:35:07 | c748259 |
| 36 | Dashboard contact-info lookup reply never persisted to chat_messages -- breaks pronoun resolution for that handler | watson | 2026-07-18 14:35:07 | c748259 |
| 35 | Dashboard SMS flow sent literal bare pronoun ("that"/"it"/"this") instead of resolving prior context | watson | 2026-07-18 14:26:00 | bc1aca4 |
| 34 | Dashboard chat "text that to me" resolved recipient to Andrea Venuto instead of Bill Yomes | watson | 2026-07-18 13:59:30 | 9709b2e |
| 33 | reminder_create and task_create gated with YES/NO confirmation for consistency | watson | 2026-07-18 13:16:10 | b21970f |
| 32 | task_done had no confirmation gate before a fuzzy LIKE match silently marked tasks done | watson | 2026-07-18 13:11:40 | 74df41c |
| 31 | calendar_busy intent had no confirmation gate before writing to Google Calendar | watson | 2026-07-18 13:08:10 | f962909 |
| 30 | Telegram classifier misrouted plain greetings ("Good morning Watson") to calendar_query instead of general chat | watson | 2026-07-18 08:40:00 | cf6a710 |
| 29 | Telegram classify()/handle_text timeouts (10s/15s) structurally too short for classifier prompt prefill (up to 38.4s) on CPU host | watson | 2026-07-18 07:35:00 | bfdf569 |
| 28 | Telegram classify()/skill-router Ollama calls had no num_predict cap; skill router used qwen2.5-coder:7b for simple label routing | watson | 2026-07-18 02:06:38 | 3352336 |
| 27 | Telegram wrap_up() passes string session_id, causes silent hallucinated writes to memory/relational.md while reporting false success | watson | 2026-07-17 22:45:37 | ebe4616 |
| 25 | Dashboard chat Ollama fallback timeout (30s) too short for memory-context-injected prompts (~1183 tokens, ~30s prefill alone on CPU) | watson | 2026-07-17 20:28:53 | 291c27c |
| 20 | Intent classifier cold-start exceeds 15s Telegram handler timeout | watson | 2026-07-17 17:52:11 | 56d60dd |
| 19 | jobs/ask.py synthesize() 120s timeout fails on cold-start qwen2.5-coder:7b load | watson | 2026-07-16 01:04:11 | d124dc187bae87a954af14a0227117832c1da983 |
| 18 | jobs/ask.py hardcodes nonexistent model phi3:mini -- KB synthesis always fails | watson | 2026-07-16 00:40:06 | be7ab8c9971fcd4ad41baa2ec9c83e1e9ae33805 |
| 16 | Home dashboard task list had no priority/due-date edit controls | watson | 2026-07-14 13:18:08 | 2427104 |
| 15 | wcky /meet booking store call hit unreachable Tailscale IP | watson+wcky | 2026-07-13 14:39:48 | watson:e627581 / wcky:a0c49d5 |
| 14 | same_name_diff_email conflicts never populated new_member_id, breaking Telegram merge | watson | 2026-07-13 00:53:41 | a0417bf |
| 4 | /draft page UI copy stale | wcky | 2026-07-12 21:08:49 | 77a069f |
| 9 | Bare blocking requests.post() calls in async Telegram bot context | watson | 2026-07-09 10:18:11 | 1e52f34 |
| 3 | KB search hang - oversized prompt on wrong model | watson | 2026-07-09 00:13:29 | 9ccb9ef |
| 2 | KB search 500 - sentence_transformers/huggingface-hub mismatch | watson | 2026-07-09 00:13:29 | f00e78d |
| 1 | Chat-stream timeout | watson | 2026-07-09 00:13:29 | 35c03ca |
