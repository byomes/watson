# Watson Bug Tracker
_Auto-generated nightly from bug_tracker. Source of truth is the database — do not hand-edit this file, changes will be overwritten._
Last generated: 2026-09-26 02:10

## Open (3)
| ID | Title | Repo | Discovered |
|---|---|---|---|
| 195 | gemma4:e4b intent classifier produced stuck/orphaned llama-server runners | watson | 2026-09-23 16:13:22 |
| 194 | Picnic event_registrations row with blank name | watson | 2026-09-23 02:29:23 |
| 21 | Ollama/gemma3:4b transient severe slowdown under rapid back-to-back requests (10-42s), self-resolving | watson | 2026-07-17 17:36:05 |

## Recently Resolved (last 30 days)
| ID | Title | Repo | Resolved | Commit |
|---|---|---|---|---|
| 198 | Banquet RSVP questions fell through to paid LLM; RSVP counts included declines | watson | 2026-09-25 20:09:15 | ead92f8 |
| 197 | Stale Getaway Search links left in dashboard after tool deletion | watson | 2026-09-25 03:01:52 | 0a15872 |
| 196 | State of the Church cited future/unaware-of-past events for attendance reasoning | watson | 2026-09-25 01:21:29 | 1a115fb |
| 22 | Ollama OLLAMA_MAX_LOADED_MODELS=1 forces single-model residency, causing classifier/general-chat model thrash | watson | 2026-09-23 02:55:28 |  |
| 55 | watson-codeagent.service is live but broken and undocumented | watson | 2026-09-23 02:51:36 |  |
| 58 | missed_report.py cron path missing slash, silently failed weekly | watson | 2026-09-23 02:45:53 | 02a8855 |
| 193 | Event-signup fast path missed questions with glued filler prefix | watson | 2026-09-23 02:29:23 | 9b1d397 |
| 192 | Event signups store blank registrant name when signup email has none | watson | 2026-09-20 19:00:10 | 6fbc2fee56c8ebe5dca7baded54026fed3748376 |
| 40 | Backlog: dashboard chat has no durable session/history -- session_id never sent to /api/chat/stream by any caller | watson | 2026-09-20 01:42:26 | 6d347a2 |
| 24 | Dashboard chat runs on Ollama by design (no ANTHROPIC_API_KEY) — stale claude-sonnet-4-6 model strings need updating if Claude is ever reactivated | watson | 2026-09-20 01:42:26 | 6d347a2 |
| 10 | chat_stream() missing polish this:/kb:/shepherding: directive intercepts (present in /api/terminal, absent in /api/chat/stream — falls through to Ollama chat) | watson | 2026-09-20 01:42:26 | 6d347a2 |
| 182 | devdispatch auto-merge fails on draft PRs | watson | 2026-09-20 01:32:46 | 0875c29 |
| 181 | Password vault stored plaintext + auth bypass | watson | 2026-09-20 01:25:26 | 545492f |
| 116 | Team-chat "when did X last attend" regex over-captures the word "last" into the person name | watson | 2026-09-20 01:25:09 | d16a99de |
| 115 | Telegram: LOW-confidence general intent produced a pointless confirm prompt; write intents double-confirmed | watson | 2026-09-20 01:25:09 | 3afff17d |
| 114 | Telegram intent classifier misroutes reflective/advice questions into calendar actions | watson | 2026-09-20 01:25:09 | 3afff17d |
| 147 | jobs.browser: goto_safe failed for https://www.beenverified.com/people/mikhaela-molanders/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 146 | jobs.browser: goto_safe failed for https://www.beenverified.com/people/emily-yomes/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 145 | jobs.browser: goto_safe failed for https://www.beenverified.com/people/micah-yomes/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 144 | jobs.browser: goto_safe failed for https://www.beenverified.com/people/melanie-yomes/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 143 | jobs.browser: goto_safe failed for https://www.beenverified.com/people/william-yomes/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 141 | jobs.browser: goto_safe failed for https://www.beenverified.com/people/mikhaela-molanders/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 140 | jobs.browser: goto_safe failed for https://www.beenverified.com/people/emily-yomes/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 139 | jobs.browser: goto_safe failed for https://www.beenverified.com/people/micah-yomes/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 138 | jobs.browser: goto_safe failed for https://www.beenverified.com/people/melanie-yomes/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 137 | jobs.browser: goto_safe failed for https://www.beenverified.com/people/emily-yomes/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 136 | jobs.browser: goto_safe failed for https://www.beenverified.com/people/micah-yomes/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 135 | jobs.browser: goto_safe failed for https://www.beenverified.com/people/melanie-yomes/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 134 | jobs.browser: goto_safe failed for https://www.beenverified.com/people/william-yomes/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 87 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=mikhaela+molanders&state=de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 86 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=emily+yomes&state=de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 85 | jobs.browser: goto_safe failed for https://www.mylife.com/emily-yomes/de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 84 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=micah+yomes&state=de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 83 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=melanie+yomes&state=de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 82 | jobs.browser: goto_safe failed for https://www.mylife.com/melanie-yomes/de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 81 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=william+yomes&state=de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 80 | jobs.browser: goto_safe failed for https://www.mylife.com/william-yomes/de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 79 | jobs.browser: goto_safe failed for https://www.mylife.com/melanie-yomes/de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 78 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=william+yomes&state=de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 77 | jobs.browser: goto_safe failed for https://www.ussearch.com/people/william-yomes/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 76 | jobs.browser: goto_safe failed for https://www.mylife.com/william-yomes/de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 73 | jobs.browser: goto_safe failed for https://www.mylife.com/melanie-yomes/de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 72 | jobs.browser: goto_safe failed for https://nuwber.com/search?name=william+yomes&state=de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 71 | jobs.browser: goto_safe failed for https://www.ussearch.com/people/william-yomes/de/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 70 | jobs.browser: goto_safe failed for https://www.mylife.com/william-yomes/de | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 69 | jobs.browser: goto_safe failed for https://www.peoplefinders.com/manage | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 68 | jobs.browser: goto_safe failed for https://www.intelius.com/opt-out | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 67 | jobs.browser: goto_safe failed for https://nuwber.com/removal/link | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 66 | jobs.browser: goto_safe failed for https://control.radaris.com/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 65 | jobs.browser: goto_safe failed for https://www.ussearch.com/opt-out/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 64 | jobs.browser: goto_safe failed for https://radaris.com/page/how-to-remove | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 46 | jobs.browser: goto_safe failed for https://this-domain-does-not-exist-watson-test-12345.invalid/ | watson | 2026-09-20 01:22:46 | 0cb8e656/3074151f |
| 191 | Subsplash calendar monitor 403s on Playwright HeadlessChrome UA | watson | 2026-09-19 18:31:12 | edcf732 |
| 190 | False OneDrive-backup-FAILED alert from status-check race against growing sermonshots_clips data | watson | 2026-09-19 11:28:42 | d2e3d33 |
| 189 | Team Chat event registration question with zero registrations fell through to paid LLM | watson | 2026-09-18 19:12:01 | e508122 |
| 188 | Events Q&A falsely claims a tracked event with 0 registrants isn't tracked | watson | 2026-09-18 19:09:37 | 82379bb |
| 187 | Event signup COUNT returns bare em dash instead of 0 for zero-registrant events | watson | 2026-09-18 19:09:37 | 82379bb |
| 186 | Event-signup matching crashes whenever any event is tracking_active | watson | 2026-09-18 18:17:20 | ebd6daf |
| 185 | New-event notice regex misses past-tense phrasing, silently drops Kaci's event | watson | 2026-09-18 17:37:33 | 5e363e4 |
| 184 | False OneDrive backup failure alerts | watson | 2026-09-18 10:56:15 | e57de8a |
| 183 | Team Chat event-signup question fell through to paid LLM when no tracked event matched | watson | 2026-09-18 03:35:09 | a32977d |
| 180 | Team Chat LAST ATTENDED query rejected -- referenced raw connect_cards, not the whitelisted view | watson | 2026-09-16 10:19:24 | 74bcb89 |
| 179 | bot.py _extract_team_lookup (Bill's own DM path) rejects "when is" phrasing -- same bug as cdb_query.py, different file | watson | 2026-09-16 10:17:21 | fa38f91 |
| 178 | LAST ATTENDED/LAST MISSED BY NAME fast path rejects "when is" phrasing | watson | 2026-09-16 10:14:44 | 2a9d825 |
| 177 | fast_path_suggestions auto-merge exception applied to routing changes, not just lookups | watson | 2026-09-16 10:08:16 | 90966ec |
| 176 | Team Chat pings Bill / burns a paid Claude call on explicit no-reply-needed test messages | watson | 2026-09-16 10:04:10 | 671fe8c |
| 175 | fast_path_suggestions auto-apply can insert a dead bracket-literal trigger | watson | 2026-09-15 21:35:50 | 21f8148 |
| 174 | Deacon app FamilySection picker hides already-grouped household mates as spouse candidates | watson-tools | 2026-09-15 18:19:23 | 2ad6efa |
| 173 | Spouse-marking phrasings ("make X Y's wife", "X and Y are married") not recognized | watson | 2026-09-15 18:12:48 | 775e49a |
| 172 | Testing mistake during fast-path-per-call feature build: live accidental auto-apply + watermark poisoned | watson | 2026-09-15 00:15:12 | 9357adc83f67e3daa7fb2f6a694984ac1dc5c371 |
| 171 | jobs/events/pattern_match.py (commit 39e9424) attend/coming triggers collided with attendance questions | watson | 2026-09-14 22:54:11 | c1c538eafb1d48063bb1f0ff764a168f1f6275a4 |
| 170 | 'Both campuses' fast-path/LLM collision gave a nonsense attendance answer | watson | 2026-09-14 22:54:11 | c1c538eafb1d48063bb1f0ff764a168f1f6275a4 |
| 169 | Events questions always hit Claude API — no fast path existed | watson | 2026-09-14 22:13:17 | 39e9424446ca2d0ee0fe1835b03748ebe2914a78 |
| 168 | Event-signup/email-triage Ollama classifiers 404 (llama3.2:1b pruned) | watson | 2026-09-14 21:52:35 | 4a0316904b120a8dd4e8676cba2fe69b49ce6818 |
| 167 | Name lookup missed nicknames and lost context on disambiguation follow-up | watson | 2026-09-14 17:06:41 | b573a0b |
| 166 | batch.py ModuleNotFoundError: No module named jobs when run directly | watson | 2026-09-14 13:02:20 | ee6f1bf |
| 165 | generate.py overwrote historical sermon dates with ingestion date | watson | 2026-09-13 20:39:58 | 9e8295e |
| 164 | Archive-mode sermon pipeline never ships transcripts to Beelink KB | watson | 2026-09-13 20:36:44 | 1a4c9cf |
| 163 | duplicate_review.merge_members double-counts attendance on collision | watson | 2026-09-13 20:12:45 | d9e9c9c |
| 162 | Draft-reply approval Telegram message never showed the original email | watson | 2026-09-11 20:10:57 | ad136ae |
| 161 | Disposition keyword matching false-positived on substrings | watson | 2026-09-11 20:06:09 | aa869ee |
| 160 | Email/event-signup Telegram triage never showed real content, replies discarded | watson | 2026-09-11 20:06:09 | aa869ee |
| 159 | Live loop crashes silently on Alpaca data API outage | watson | 2026-09-11 16:43:39 | fde831d |
| 158 | Intraday flatten-by-close fails on sessions with real data gaps | watson | 2026-09-11 13:00:17 | 66331f6 |
| 157 | Intraday flatten order submitted on literal last bar never fills | watson | 2026-09-11 12:36:11 | f0ef17f |
| 156 | Intraday flatten-by-close let new entries fire in the closing window | watson | 2026-09-11 12:36:11 | f0ef17f |
| 155 | ma_crossover/mean_reversion/momentum templates use 1-share default order size, not fully-invested | watson | 2026-09-11 11:36:25 | 15360dd |
| 154 | Trading holdout pass bar gameable by inactive strategies | watson | 2026-09-11 03:39:55 | 1a6ec67 |
| 153 | Savings tab: This Month != All-Time | watson | 2026-09-10 15:48:03 | 3ecd252 |
| 152 | "assign X to me" would write literal "Bill Yomes" into members.deacon | watson | 2026-09-08 19:07:44 | 5ac7aa9 |
| 151 | data_chat generated wrong SQL for partner/deacon-gap questions | watson | 2026-09-08 12:44:30 | 6083f25 |
| 150 | UnboundLocalError crashed all leader/team-chat Telegram messages | watson | 2026-09-08 12:40:15 | 114d148 |
| 142 | Leadership-only prayer requests written as leadership_only=0 | watson | 2026-09-07 13:17:20 | 52145b1 |
| 98 | connect_cards intake truncates multi-line question/comment field | watson | 2026-09-06 03:47:09 | a90d6d5 |
| 97 | Dashboard SSE chat KB pre-check imports nonexistent kb_search.run | watson | 2026-09-06 03:47:09 | bef91e6 |
| 52 | jobs.skills.kb_search has no run() function but chat_stream imports it | watson | 2026-09-06 03:47:09 | bef91e6 |
| 26 | Telegram wrap_up() passes string session_id, causes silent hallucinated writes to memory/relational.md while reporting false success | watson | 2026-09-06 03:47:09 | ebe4616 |
| 133 | Curator: wrong-book match when titles differ only by a negation word | watson | 2026-09-06 00:49:40 | cc032e7c228901d719cb748b11d67db84daba6fd |
| 132 | Curator: page_count=0 rendered as literal 0 (React falsy-render gotcha) + Google Books pageCount:0 data bug | watson+curator | 2026-09-06 00:27:33 | watson:1ebe4c5+0359042, curator:92ee3ec |
| 131 | Curator: extend book-page-only rule to CSM/SpicyBooks/FaeShelf/StoryGraph | watson | 2026-09-06 00:18:56 | ff7d7db |
| 130 | Curator: romance.io fallback attached wrong-book content (While Raven incident) | watson | 2026-09-06 00:07:07 | 659b72e |
| 129 | Curator: add Google Books metadata source + fix key-leak-in-log before it shipped | watson | 2026-09-05 23:35:36 | 909f7cb |
| 128 | Curator: add StoryGraph as a 5th trusted spice-research source | watson | 2026-09-05 23:13:04 | a46f321 |
| 127 | Curator Search-by-Photo: failed identification fed into live research, fabricating spice data | watson | 2026-09-05 22:55:11 | 72bc99f |
| 126 | kb_export_link_cleanup.py cron entry documented but never installed | watson | 2026-09-05 21:52:20 | 997d748 |
| 119 | skillbuilder/router.py LLM fallback has an 8s timeout, too short for its real ~6-8k token prompt | watson | 2026-09-05 21:51:11 | 9d86b36 |
| 117 | reflect.py _load_messages can scramble transcript order on same-second timestamps | watson | 2026-09-05 21:51:11 | 2ac41c1 |
| 118 | skill_audit run_audit() prompt likely exceeds model context window, silently dropping task instructions | watson | 2026-09-04 02:11:31 | 7fb19e1 |
| 121 | Classifier hallucinates wrong intent under Ollama contention instead of degrading honestly | watson | 2026-09-04 02:10:55 | 7fb19e1 |
| 113 | watson-tools dispatcher crashed intermittently on DNS resolution to the Watson backend | watson-tools | 2026-09-01 13:09:37 | 08ed7a9 |
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
