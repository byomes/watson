# Watson Bug Tracker
_Auto-generated nightly from bug_tracker. Source of truth is the database — do not hand-edit this file, changes will be overwritten._
Last generated: 2026-09-01 02:10

## Open (37)
| ID | Title | Repo | Discovered |
|---|---|---|---|
| 98 | connect_cards intake truncates multi-line question/comment field | watson | 2026-08-24 13:04:31 |
| 97 | Dashboard SSE chat KB pre-check imports nonexistent kb_search.run | watson | 2026-08-24 12:44:22 |
| 87 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=mikhaela+molanders&state=de | watson | 2026-08-21 13:46:22 |
| 86 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=emily+yomes&state=de | watson | 2026-08-21 13:45:53 |
| 85 | jobs.browser: goto_safe failed for https://www.mylife.com/emily-yomes/de | watson | 2026-08-21 13:45:33 |
| 84 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=micah+yomes&state=de | watson | 2026-08-21 13:45:11 |
| 83 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=melanie+yomes&state=de | watson | 2026-08-21 13:44:43 |
| 82 | jobs.browser: goto_safe failed for https://www.mylife.com/melanie-yomes/de | watson | 2026-08-21 13:44:23 |
| 81 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=william+yomes&state=de | watson | 2026-08-21 13:44:00 |
| 80 | jobs.browser: goto_safe failed for https://www.mylife.com/william-yomes/de | watson | 2026-08-21 13:43:39 |
| 79 | jobs.browser: goto_safe failed for https://www.mylife.com/melanie-yomes/de | watson | 2026-08-21 09:31:27 |
| 78 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=william+yomes&state=de | watson | 2026-08-21 09:31:05 |
| 77 | jobs.browser: goto_safe failed for https://www.ussearch.com/people/william-yomes/de/ | watson | 2026-08-21 09:30:49 |
| 76 | jobs.browser: goto_safe failed for https://www.mylife.com/william-yomes/de | watson | 2026-08-21 09:30:25 |
| 73 | jobs.browser: goto_safe failed for https://www.mylife.com/melanie-yomes/de | watson | 2026-08-20 15:03:13 |
| 72 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=william+yomes&state=de | watson | 2026-08-20 15:02:51 |
| 71 | jobs.browser: goto_safe failed for https://www.ussearch.com/people/william-yomes/de/ | watson | 2026-08-20 15:02:36 |
| 70 | jobs.browser: goto_safe failed for https://www.mylife.com/william-yomes/de | watson | 2026-08-20 15:02:11 |
| 69 | jobs.browser: goto_safe failed for https://www.peoplefinders.com/manage | watson | 2026-08-20 12:43:12 |
| 68 | jobs.browser: goto_safe failed for https://www.intelius.com/opt-out | watson | 2026-08-20 12:42:45 |
| 67 | jobs.browser: goto_safe failed for https://nuwber.com/removal/link | watson | 2026-08-20 12:42:23 |
| 66 | jobs.browser: goto_safe failed for https://control.radaris.com/ | watson | 2026-08-20 12:42:17 |
| 65 | jobs.browser: goto_safe failed for https://www.ussearch.com/opt-out/ | watson | 2026-08-20 12:42:02 |
| 64 | jobs.browser: goto_safe failed for https://radaris.com/page/how-to-remove | watson | 2026-08-20 12:41:52 |
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
| 10 | chat_stream() missing polish this:/kb:/shepherding: directive intercepts (present in /api/terminal, absent in /api/chat/stream — falls through to Ollama chat) | watson | 2026-07-11 16:12:30 |

## Recently Resolved (last 30 days)
| ID | Title | Repo | Resolved | Commit |
|---|---|---|---|---|
| 112 | connect_cards email_reports.py sent Bill duplicate copies of every report | watson | 2026-08-31 15:43:25 | 74ced40 |
| 111 | connect_cards intake.py only scanned INBOX, silently dropping spam-misclassified cards | watson | 2026-08-31 15:43:25 | 74ced40 |
| 110 | campus_classifier.py overwrites Inactive campus_preference every Monday | watson | 2026-08-31 11:14:53 | ebe5de2 |
| 105 | Connect-card intake created duplicate attendance rows | watson | 2026-08-31 02:09:11 | 265a8e9 |
| 109 | watson_recover.sh: systemd install step swept up unrelated .service files | watson | 2026-08-30 22:58:00 | c342d86 |
| 108 | watson_recover.sh: ollama binary never installed; installer needs curl/zstd not in apt list | watson | 2026-08-30 22:54:00 | ecd0478 |
| 107 | watson_recover.sh: tailscale not installable via plain apt on stock Ubuntu | watson | 2026-08-30 22:41:00 | 8e7d2a9 |
| 106 | watson_recover.sh: missing cron package aborts recovery at crontab restore | watson | 2026-08-30 22:32:00 | 2a077ed |
| 104 | Thesis Tracker dashboard card fails to load (missing citations API route) | watson | 2026-08-29 14:17:03 | 9a62ce9 |
| 103 | cat/connect API route was unguarded — direct-POST-able while draft, real Brevo creds active | watson-tools | 2026-08-28 04:28:26 | 7f7071b |
| 102 | cat/connect draft gate was decorative — custom-type page never checked public_tools.status | watson-tools | 2026-08-28 04:28:26 | 741eb17 |
| 101 | get_archive skill trigger too strict, silently unroutable from Claude.ai | watson | 2026-08-26T09:12:36 | 3b9235d |
| 100 | MCP run_watson_skill fails on kb/kb_search: missing positional argument | watson | 2026-08-24 13:09:59 | e6f674b |
| 99 | connect_cards intake truncates multi-line question/comment field | watson | 2026-08-24 13:09:59 | a90d6d5 |
| 96 | run_watson_skill/list_watson_skills MCP tools were live in production but never committed to git | watson | 2026-08-24 12:21:16 | eb40d7c |
| 95 | devdispatch: dispatched session deviating from -w worktree branch loses/misreports real work (wcky retreats cluster, 2026-08-16) | watson | 2026-08-24 01:53:25 | c78ff9d8a29f21d6ad14e01502f3465429c154fe |
| 89 | email_reply/reader.py replied to Connect Card submissions | watson | 2026-08-23 21:11:57 | 5709440 |
| 94 | skill_tester.py audit harness gave false failures for 7+ skills | watson | 2026-08-23 17:37:02 | 0fc99348a250987762bdf38b287db0962285791c |
| 93 | pastoral_notes skill registered against a nonexistent function | watson | 2026-08-23 17:37:02 | 0fc99348a250987762bdf38b287db0962285791c |
| 92 | Dashboard 'state of church report' quick-command didn't reach the report generator | watson | 2026-08-23 17:37:02 | 0fc99348a250987762bdf38b287db0962285791c |
| 91 | Dashboard 'check logs' command tailed a log file that no longer exists | watson | 2026-08-23 17:37:02 | 0fc99348a250987762bdf38b287db0962285791c |
| 90 | calendar_query skill unregistered — 'what's on my calendar' always failed | watson | 2026-08-23 17:37:02 | 0fc99348a250987762bdf38b287db0962285791c |
| 75 | Privacy Guard scan: goto_safe() fails OPEN on robots.txt 403/non-200 instead of fail-closed | watson | 2026-08-21 13:52:28 | 98367e7 |
| 74 | Privacy Guard scan: bug_tracker logging fails with "database is locked" mid-run | watson | 2026-08-21 13:52:28 | 98367e7 |
| 63 | Comms Desk Facebook posts never dispatched | watson | 2026-08-17 19:41:44 | cd04825 |
| 62 | Curator ChatGPT import: verbatim-excerpt guarantee relied only on the LLM prompt | watson | 2026-08-08 21:18:52 | ef676d7 |
| 61 | Curator ChatGPT import: hard extraction failure silently lost the pasted research text | watson | 2026-08-08 21:18:52 | 3245bef |
| 60 | OneDrive backup: watson.db snapshot fails intermittently with SQLite 'database is locked' | watson | 2026-08-08 12:00:06 | 8996f873dd1e342cb5b4faed7f5d5eadd65cc205 |
| 59 | devdispatch _open_pr collides with dispatched session's own PR, misreporting real work as failed | watson | 2026-08-06 04:29:27 | 4793ce6 |
| 17 | Export CSV button downloads HTML instead of CSV | watson | 2026-08-06 04:29:27 | f50910d |
| 56 | headcount_sync.py never wired into crontab — Wilmington headcount data missing since 7/27 | watson | 2026-08-04 13:53:32 | 3b1abf94b4e2f87f0675f90be19dc965285e2688 |
| 57 | devdispatch _worktree_path() mismatches CLI worktree dirname sanitization | watson | 2026-08-04 12:36:50 | c3db18f |
| 54 | Suggest Fonts narrow stage timed out silently on every real run | watson | 2026-08-02 18:01:26 | 30161e4 |
