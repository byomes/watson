# Build Spec: Claude Code Dispatch via MCP (v2 — post-audit)

**Origin:** Voice conversation, 2026-08-03. Goal: eliminate copy/paste between
Claude.ai and Claude Code on the Beelink. Bill talks through a task with
Claude.ai, Claude.ai dispatches it directly, and Bill can ask "is it done?"
later in the same chat to get results back.

**Audit finding (2026-08-03):** jobs/code_agent/ and jobs/dev/code_agent.py
are dead — discard, don't extend. watson-codeagent.service is live but
broken (Gmail scope error every 60s, zero successful runs ever) and should
be disabled — flagged separately below since disabling is outside current
sudo scope.

## 1. Decisions locked in

- PR-only completion. No auto-restart, no auto-deploy. Bill merges/pulls/
  restarts manually. **Superseded in part 2026-08-06 — see note below.**
- Build fresh. Do not extend jobs/code_agent/ or jobs/dev/code_agent.py.
- Use the CLI's own native mechanisms instead of hand-rolled equivalents:
  - `claude --bg --permission-mode bypassPermissions -w <branch-name>
    --output-format json -p "<spec>"` (or without --bg for a synchronous
    variant) to launch, using the CLI's built-in worktree isolation (-w)
    instead of manual git checkout -b.
  - `claude agents --json --cwd <path>` for status polling — prefer this
    over a hand-rolled DB poll loop where it covers the need. Still keep a
    thin claude_code_jobs table in watson.db to map job_id -> repo/branch/
    PR-url/Telegram-chat, since `claude agents` won't know about our PR
    step or notification needs.
  - --json-schema to force structured {status, pr_url, summary} output
    where useful.
  - --max-budget-usd as a sane per-job cap — pick a default, flag it for
    Bill to adjust.

**2026-08-06 — chat-based merge approval added.** New `merge_claude_code_job`
tool (`jobs/devdispatch/api.py`), a deliberate, approved change to the
"Bill merges manually" line above — not an oversight. This moves the
approval *gate* from GitHub's UI into chat; it does not remove it. Bill
still has to make an explicit, per-job decision to merge — the tool only
ever fires on an explicit per-job approval in that conversation turn, never
proactively, automatically on job completion, or as a batch — and it still
runs the same checks Bill would eyeball on GitHub before clicking Merge
(PR open, no conflicts, no failing checks) before it will act. `pr_url`/
merge state is tracked via a new nullable `merged_at` column on
`claude_code_jobs`; `dispatch_claude_code_job`'s own completion path is
unchanged — it still only opens the PR and stops.

## 2. New MCP endpoint

- New Flask blueprint on watson-dashboard.service (port 5200), route
  /mcp/devdispatch, riding the existing Tailscale Funnel.
- Auth: new MCP_DISPATCH_API_KEY env var, same shared-secret header pattern
  as Writing Room/bodyrec.
- Registered as a custom connector in the Claude.ai project once live.

## 3. Two tools

### dispatch_claude_code_job
Input: spec (string), repo (watson/wcky/watson-admin/watson-ui/fms/bodyrec),
optional branch_name.
Behavior: insert claude_code_jobs row (id, spec_text, repo, branch, status,
created_at, pr_url, log_path) -> launch via claude --bg with worktree
isolation, never main -> on completion, push branch + open PR via GitHub
REST API (GITHUB_TOKEN already in .env) -> update row to done + Telegram
summary -> return job_id immediately, non-blocking.

### check_claude_code_job
Input: job_id.
Behavior: cross-reference our table with `claude agents --json`. Return
running+log-tail, or done+PR-url+summary, or failed+log-tail.

## 4. Approval pattern

Same as every other Watson system: nothing lands on main without Bill's
review. Dispatcher stops at "PR opened, Telegram sent."

## 5. Separate follow-up (not part of this build)

- Disable watson-codeagent.service — needs a sudo grant beyond current
  restart-only scope, or Bill running it by hand. File a bug_tracker entry:
  undocumented live service, broken Gmail auth scope, dead code path, zero
  successful runs since 2026-06-05.

## 6. Build checklist

- [x] SSH/restart discrepancy resolved — PR-only
- [x] Audited jobs/code_agent/ — discarded, not extended
- [x] Confirmed CLI invocation pattern against `claude --help` (2.1.221) —
      --bg / --permission-mode bypassPermissions / -w / --output-format json
- [x] claude_code_jobs table + migration
- [x] /mcp/devdispatch blueprint, registered on dashboard app
- [x] MCP_DISPATCH_API_KEY added to .env, documented in WATSON_ARCHITECTURE.md
- [x] dispatch_claude_code_job using claude --bg + -w worktree isolation
- [x] check_claude_code_job cross-referencing claude agents --json
- [x] Branch-only enforcement (hard block on main)
- [x] GitHub PR creation via GITHUB_TOKEN
- [x] Telegram notification on completion/failure
- [x] bug_tracker entry for watson-codeagent.service (separate from this build)
- [x] Register connector in Claude.ai project once live
- [x] Update FILE_MAP.md / WATSON_ARCHITECTURE.md (or let nightly docs job
      pick it up)

Once the file is created, proceed through the checklist in order. Diffs
shown before any commit, per standing convention. Stop and report back
after the table + blueprint skeleton are in place, before wiring the actual
claude --bg invocation, so I can sanity-check the exact command before it
starts spawning real sessions.

**2026-08-04 — done.** Connector successfully authorized and connected
from Claude.ai. Required an OAuth 2.1 authorization_code + PKCE shim
(client_credentials confirmed unsupported by Claude.ai's connector) plus
root-level `/authorize` and `/token` proxy routes to work around a
confirmed Claude.ai connector bug (anthropics/claude-ai-mcp #82, #283,
#644) — neither was anticipated in this original spec. See
WATSON_ARCHITECTURE.md's MCP Claude Code Dispatcher section for the full
current state, including known gaps.
