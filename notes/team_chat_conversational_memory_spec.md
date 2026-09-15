# Team Chat Conversational Memory — Improvement Spec

*Spec only. No code changed. Written 2026-09-15 after Kaci Gravatt's first real
exchange with Watson exposed the gap this spec addresses.*

---

## 1. What happened (factual, from live logs/DB)

`data/watson.db`, `telegram_log`, Kaci's first-ever exchange with Watson:

```
id=425 out  "You're connected to Watson, Dr. Bill's digital assistant."
id=426 in   "Hi Watson, this is Kaci. I handle digital communications and
             event registrations for Catalyst."
id=427 out  "Hello Kaci, nice to assist you with any digital-related tasks
             or questions you may have about the church's online presence.
             What can I help..."
```

Kaci's message is an introduction — a statement, not a question, and not a
request for anything. Watson's reply doesn't acknowledge what she actually
said (her role, "digital communications and event registrations") and
immediately pivots to "what can I help you with," the same register it would
use for literally any message, including a genuine question.

A second, independent symptom of the same underlying gap: `logs/fast_path_suggestions.log`
shows the weekly suggestion reviewer treating this exact message as if it were
an unanswerable *question* worth building a fast-path SQL pattern for:

```
08:42:56 review_single_question: reviewing id=36 asker='Kaci Gravatt'
         q='Hi Watson, this is Kaci. I handle digital communications and
         event registrations for Catalyst.'
08:44:01 id=36 needs a real decision -- sent to Telegram (suggestion_id=22)
```

That's `jobs/analytics/fast_path_suggestions.py` sending Bill a Telegram
suggestion asking him to judge whether "Hi Watson, this is Kaci..." deserves
a hand-built query pattern — because everything that reaches
`core/claude_tier.py`'s `analytics.data_chat` job gets logged and reviewed as
a question, with no check for whether it was actually one.

## 2. Current architecture (read from code, not assumed)

Team-member messages route through `bot/bot.py`'s `compute_team_chat_reply(name, text)`,
which tries, in order: pending-clarification resume → person/classroom/calendar/web-metric
regex extractors → `jobs.analytics.data_chat.answer_data_question()`. If
`data_chat` says `on_topic=False` (no DOMAIN matched — exactly what happens
for an introduction like Kaci's), it falls to `_get_team_reply_sync()`:

```python
resp = _req.post("http://localhost:11434/api/chat", json={
    "model": "llama3.2:3b",
    "messages": [
        {"role": "system", "content": TEAM_CHAT_SYSTEM},
        {"role": "user", "content": text},
    ],
    "stream": False,
})
```

**Every single call is stateless.** No prior turns are ever sent — not from
this conversation, not from anything Kaci (or any other leader) has said
before. `TEAM_CHAT_SYSTEM` (`config/settings.py`) has no framing for
"statement vs. question" either; it just says to hold "a general
conversation" and answer general questions, so with no context to react to,
the model defaults to generic servant-register boilerplate for every message
shape.

The *only* per-person state that exists anywhere in this whole path is
`data_chat.py`'s `_pending_clarifications` dict — and it holds exactly one
narrow thing: candidate rows from an ambiguous person lookup, keyed by
`asker_name`, TTL'd at 300s, capped at 200 entries with oldest-first
eviction. It was built 2026-09-11 to fix a one-word disambiguation reply
("Crook") falling through to general chat with no memory of the question it
answered. It does not, and was never meant to, hold general conversational
context.

`data_chat.answer_data_question()` also runs statelessly — each call gets
only the current message (`resolved_question`), no prior turns, no memory
that (for example) an asker already told Watson their role or what they're
working on.

## 3. Root cause

Two related but distinct gaps, both stemming from "no conversational memory
per person":

1. **No prior-turn context is ever sent to the model**, so it can't tell a
   social opener/introduction/statement from a request — it has nothing to
   react to except the single incoming line, so it always answers in the
   same "how can I help" register regardless of what was actually said, and
   can't recall anything a leader told it (like Kaci's role) on the very
   next reply.
2. **`fast_path_suggestions.py` has no question/non-question gate** before
   flagging a `claude_tier_spend_log` row for Bill's weekly review — it
   treats every row that reached `data_chat`'s Claude/Ollama call as a
   "question" that might deserve a hand-built pattern, even when the
   content was never a question at all.

Neither is really "Watson thinks everything is a question" in the sense of
misrouting — `data_chat` correctly returned `on_topic=False` for Kaci's
message. It's that nothing downstream of that decision has any sense of
*conversational turn-taking*, so the reply-generation and the
suggestion-review paths both default to question-shaped handling because
that's the only shape they know.

## 4. Proposed design

### 4a. Per-leader rolling conversation buffer

Add a small in-process buffer, same shape and identity key as the existing
`_pending_clarifications` pattern (per `feedback_watson_per_chat_context.md`:
per-asker, TTL'd, size-capped, lost on restart is acceptable):

```python
# jobs/analytics/data_chat.py, alongside _pending_clarifications
_CONVERSATION_TTL_SECONDS = 1800       # 30 min of silence resets the thread
_CONVERSATION_MAX_TURNS = 8            # last N (role, content) pairs kept
_CONVERSATION_MAX_ASKERS = 200         # oldest-asker eviction, same as clarifications
_conversation_buffers: dict[str, dict] = {}   # name -> {"turns": [...], "last_at": monotonic}
```

- Keyed by the same `asker_name` already passed into `compute_team_chat_reply`
  / `answer_data_question` — no new identity resolution needed.
- One shared buffer per leader across *both* `_get_team_reply_sync` (general
  chat) and `answer_data_question` (data Q&A) turns — it's one continuous
  conversation from the leader's point of view, and Kaci asking a data
  question five minutes after introducing herself should still have that
  introduction in view.
- Append (user turn, assistant turn) after every reply is sent, same place
  `_log_tg('out', ...)` already fires in `compute_team_chat_reply`.
- Eviction: oldest-first by `last_at` past `_CONVERSATION_MAX_ASKERS`, same
  pattern as `_remember_pending_clarification`.

### 4b. Feed the buffer into both model calls

`_get_team_reply_sync` (`bot/bot.py`) and `_generate` (`data_chat.py`) both
build a `messages` list for Ollama/Claude today with exactly one user turn.
Change both to prepend the buffered turns:

```python
messages = [{"role": "system", "content": TEAM_CHAT_SYSTEM}]
messages += _conversation_buffers.get(asker_name, {}).get("turns", [])[-_CONVERSATION_MAX_TURNS:]
messages.append({"role": "user", "content": text})
```

`call_claude()` (`core/claude_tier.py`) — check its signature accepts a
multi-turn `messages` list rather than only flat `system`/`user` strings;
if not, that's a small, contained extension, not a redesign.

### 4c. Prompt update so statements aren't answered like questions

Add one explicit rule to `TEAM_CHAT_SYSTEM`:

> "Not every message is a question — a greeting, an introduction, or a
> statement about what someone does gets a natural conversational
> acknowledgment, not a request for a follow-up question. Only ask 'what can
> I help with' if the person hasn't actually told you anything to react to
> yet."

With 4a/4b in place, this rule also lets Watson reference what a leader told
it in earlier turns ("You mentioned you handle event registrations —...")
instead of just not-repeating the boilerplate once.

### 4d. Gate `fast_path_suggestions.py` on question-shape

Before `review_single_question()` (`jobs/analytics/fast_path_suggestions.py`)
sends a `claude_tier_spend_log` row to Bill as a fast-path candidate, skip
rows that aren't actually questions/requests — reuse `jobs/intent/classifier.py`'s
existing bare-greeting/social-opener judgment as a model (cheap Ollama call,
already has a "general, not worth a pattern" bucket), or a lighter heuristic
(no `?`, no imperative/request verb, first-person introduction phrasing) if a
full classifier call per row is overkill for a weekly batch job. Goal: Bill's
Telegram review queue stops getting asked to judge whether "Hi Watson, this
is Kaci" needs a SQL pattern.

## 5. Scope

In scope: `compute_team_chat_reply` / `_get_team_reply_sync` (`bot/bot.py`),
`answer_data_question` / `_generate` (`jobs/analytics/data_chat.py`),
`TEAM_CHAT_SYSTEM` (`config/settings.py`), `review_single_question`
(`jobs/analytics/fast_path_suggestions.py`).

Out of scope: Dr. Bill's own chat (`_handle_general`) — it already has richer
session machinery elsewhere (`jobs/memory/wrap_up.py`, dev-session archives)
that team-member chat was never meant to have; this spec doesn't touch it.

## 6. Open questions for Bill

- Buffer size/TTL (§4a numbers above are a starting guess, not tuned against
  real usage yet).
- Should a leader's self-described role (e.g., Kaci's "I handle digital
  communications and event registrations") get written somewhere durable —
  a `team_members`/leader profile note — so it survives past the 30-min
  buffer window and future sessions, not just this one? That's a bigger
  change (new column, a write path, probably a confirm-before-save step)
  and feels like a v2, not required to fix what happened with Kaci.
- Whether §4d's fast-path gate should be a real classifier call (more
  accurate, small recurring cost) or a cheap regex/heuristic (free, coarser)
  — leaning heuristic given this only feeds a weekly batch review, not a
  live user-facing reply.
