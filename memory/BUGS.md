# Watson Bug Tracker
_Auto-generated nightly from bug_tracker. Source of truth is the database — do not hand-edit this file, changes will be overwritten._
Last generated: 2026-10-08 02:10

## Open (7)
| ID | Title | Repo | Discovered |
|---|---|---|---|
| 220 | Email-sourced event registrations never capture custom sign-up-form answers (e.g. picnic side dish/dessert) | watson | 2026-10-03 19:37:38 |
| 208 | signup_detect.py name extraction: literal 'empty string' persisted for a real registrant | watson | 2026-09-29 03:17:01 |
| 207 | Event signup classifier stores literal 'empty string' as first/last name | watson | 2026-09-28 22:30:53 |
| 200 | wtsn.me/sms message links have no OG preview image | watson-tools | 2026-09-27 19:20:04 |
| 195 | gemma4:e4b intent classifier produced stuck/orphaned llama-server runners | watson | 2026-09-23 16:13:22 |
| 194 | Picnic event_registrations row with blank name | watson | 2026-09-23 02:29:23 |
| 21 | Ollama/gemma3:4b transient severe slowdown under rapid back-to-back requests (10-42s), self-resolving | watson | 2026-07-17 17:36:05 |

## Recently Resolved (last 30 days)
| ID | Title | Repo | Resolved | Commit |
|---|---|---|---|---|
| 227 | Connection page dropped all tracked-event signups | watson | 2026-10-07 03:47:42 |  |
| 226 | 'Which one do you mean' answers were not understood | watson | 2026-10-07 03:14:26 | 4ba2d26 |
| 225 | Team chat said 'I don't have signup numbers' for an event whose signups are stored but paused | watson | 2026-10-07 03:14:26 | 4ba2d26 |
| 224 | devdispatch auto-merge of cdb_query.py PRs had no behavioural check | watson | 2026-10-07 03:05:59 | ba8559c |
| 223 | fast_path auto-suggester validated only with ast.parse | watson | 2026-10-07 03:02:38 | 6b78da0 |
| 222 | Event fast paths unaware of imported calendar/Subsplash events | watson | 2026-10-07 03:02:38 | 1514dce |
| 221 | Team chat: 'signed up' questions answered with last Sunday's attendance | watson | 2026-10-07 03:02:38 | 098af96 |
| 219 | kidstoday_notify_donna.py crashed every week, never emailed Donna | watson | 2026-10-03 18:19:41 | 101675c |
| 218 | Telegram Log dashboard shows wrong time (false quiet-hours alarm) | watson | 2026-10-03 16:31:27 | b418bf6 |
| 215 | Monthly kids attendance undercounted for months with sparse kids_checkin coverage | watson | 2026-09-30 20:36:13 | 57a35f8 |
| 214 | Hybrid attendance breakdown triple-counted hybrid attendees | watson | 2026-09-30 20:27:35 | 5f23feb |
| 213 | SMS gateway adb heartbeat ignored working USB connection | watson | 2026-09-30 12:58:43 | 4da3f3f614182cc295ba73bb7ebb6b0d0ce38ec1 |
| 212 | cdb_query.py run() pattern-match path mislabels result columns | watson | 2026-09-30 05:00:08 | a263b54 |
| 211 | cdb_query: per-month attendance breakdown fell through to paid LLM | watson | 2026-09-29 17:55:46 | 5062ff5 |
| 210 | SMS: multi-line MMS body truncated by adb content query parser | watson | 2026-09-29 16:13:18 | 488fac80992fe9f88e445c6824b47b2a8201f993 |
| 209 | adb_client.connect_device() crashed on TimeoutExpired instead of failing over | watson | 2026-09-29 10:50:23 | c20fdfa |
| 206 | Connect-card birthday/anniversary conflicts never notified | watson | 2026-09-28 21:29:50 | 93b8e86f0b4ec2e7e59131f6b4d53a1ce59e83ce |
| 205 | FMSPC immediate KB sync trigger silently connection-refused since 2026-09-16 | watson | 2026-09-28 | 014da14 |
| 203 | Auto-applied fast-path patch bolted "exactly one time" onto the wrong block (no visit-count logic) | watson | 2026-09-27 22:02:18 | 6d85015 |
| 202 | Attendance-count fast path also defaulted to last Sunday for "this year"/"last year" | watson | 2026-09-27 21:36:09 | 0f0190c |
| 201 | Attendance-count fast path defaulted to last Sunday for any literal date/month name | watson | 2026-09-27 21:19:53 | 31aae92 |
| 199 | Event registration emails paged Bill on Telegram instead of silent intake | watson | 2026-09-26 15:51:49 | 9bdba34b5d9407f6de20c327e8fedcadbbec3ca4 |
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
