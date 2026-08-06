# DevDispatch Tool Visibility — 2026-08-05

## Question

After merging "PR #12" and restarting `watson-dashboard.service`, Claude.ai's
connector still only sees 2 tools (`dispatch_claude_code_job`,
`check_claude_code_job`) instead of the expected 3
(`merge_claude_code_job` missing). Is this a Watson-side deployment problem
or a Claude.ai connector-side caching problem?

## Bottom line

**This is a Watson-side problem, not a Claude.ai caching problem — and it's
not a deployment/restart issue either.** `merge_claude_code_job` was never
actually merged into `main`. The PR Bill merged as "PR #12" is a real,
merged PR — but it's an unrelated backup-path fix, not the
`merge_claude_code_job` work. The commit that actually adds
`merge_claude_code_job` lives only on an unmerged feature branch (in fact,
on *two* separate unmerged feature branches — see Finding 6). The live
`~/watson` checkout, the running service, and the live HTTP endpoint are all
100% consistent with each other and all correctly show 2 tools, because
that's really all that exists on `main` right now. Everything checked out
below is internally consistent; there is nothing to fix on the Claude.ai
connector side.

---

## Finding 1 — Commit checked out at `~/watson`

```
$ git log -1 --format='%H %s'
217fac5df8e3e84661699e0ef8dd3dcf2ce4622b Merge pull request #13 from byomes/worktree-devdispatch+20260806-024935
```

`217fac5` is `main`'s current tip. It **is** at/after the real PR #12 merge
commit (`d92c686`, "Merge pull request #12 from
byomes/worktree-devdispatch+20260806-022512") — `git merge-base
--is-ancestor d92c686 HEAD` returns true.

**However**, PR #12 is not what Bill believes it is. Its actual diff:

```
$ git show --stat d92c686
Merge pull request #12 from byomes/worktree-devdispatch+20260806-022512
    fix: correct local backup path to /mnt/family-storage/watson

 .devdispatch/progress.json    |  2 +-
 docs/BACKUP_SETUP.md          | 67 ++++++++++++++++---------------------------
 jobs/backup_local.py          |  8 ++++--
 memory/WATSON_ARCHITECTURE.md |  4 +--
 4 files changed, 34 insertions(+), 47 deletions(-)
```

No `jobs/devdispatch/api.py` in sight. PR #12 was the `backup_local.py` path
fix from earlier the same evening, not the devdispatch merge-tool work.

The commit that actually adds `merge_claude_code_job`
(`4dfaf09700a4fe2b04de54111d853f3bcbfd0338`, "devdispatch: add
merge_claude_code_job tool + merged_at column", modifying exactly
`jobs/devdispatch/api.py` and `jobs/devdispatch/schema.py`) is **not an
ancestor of `main`'s HEAD**:

```
$ git merge-base --is-ancestor 4dfaf09 HEAD && echo YES || echo NO
NO
```

It sits on branch `worktree-devdispatch+20260806-031322`, which diverged
from `main` right after the real PR #12 merge and was never merged back in
— no PR ever closed it into `main`. (There's a second, independent copy of
the same feature on `worktree-devdispatch+20260806-032634` — see Finding 6.)
So there is no "progress.json conflict-resolution commit" on `main` for this
feature either, because the branch carrying that conflict-resolution commit
(`d0a6dde`, "Merge main, resolve progress.json conflict") is itself off of
`main`, not merged into it.

**Read:** Bill restarted the dashboard after merging a PR — just not the PR
he thought he merged. No code drift, no stale deploy; `main` genuinely does
not contain `merge_claude_code_job`.

---

## Finding 2 — Live `_TOOLS` list on disk

```
$ PYTHONPATH=/home/billyomes/watson venv/bin/python3 -c \
  "from jobs.devdispatch.api import _TOOLS; import json; print(json.dumps([t['name'] for t in _TOOLS], indent=2))"
[
  "dispatch_claude_code_job",
  "check_claude_code_job"
]
```

Confirms Finding 1 directly at the source level: the `api.py` sitting in
`~/watson/jobs/devdispatch/` right now only defines these two tools.
`merge_claude_code_job` isn't in the source file, so it can't be in the
in-memory object either.

---

## Finding 3 — Service running from this exact checkout

```
$ systemctl show watson-dashboard.service -p ExecStart
ExecStart={ path=/home/billyomes/watson/venv/bin/python ; argv[]=/home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/dashboard/app.py ; ... status=0/0 }

$ readlink -f /home/billyomes/watson
/home/billyomes/watson
```

No symlink indirection — `/home/billyomes/watson` resolves to itself, and
the systemd unit's `ExecStart` points directly at
`/home/billyomes/watson/venv/bin/python` running
`/home/billyomes/watson/jobs/dashboard/app.py`, i.e. exactly the checkout
inspected in Findings 1–2. Service is `active`. There is no second/stale
copy of the repo the service could be running from instead.

---

## Finding 4 — Bytecode cache check

```
$ ls -la jobs/devdispatch/__pycache__/
-rw-r--r-- api.cpython-312.pyc     (mtime: 2026-08-04 09:33:59)
-rw-rw-r-- __init__.cpython-312.pyc
-rw-r--r-- schema.cpython-312.pyc

$ stat -c '%y %n' jobs/devdispatch/api.py
2026-08-04 09:33:46.465250093 -0400 jobs/devdispatch/api.py
```

`api.cpython-312.pyc` (09:33:59) is ~13 seconds **newer** than `api.py`
(09:33:46) — that's Python having compiled the current source normally, not
a stale cache lagging behind a newer source file. Nothing here suggests a
bytecode-invalidation problem; this is consistent with Finding 2 (the
running process really is reading the 2-tool source), not a symptom of a
separate caching bug. Per instructions, cache files were left untouched.

---

## Finding 5 — Live authenticated HTTP `tools/list` response

Auth mechanism (from `MCP-Claude-Code-Dispatcher-Spec.md` /
`WATSON_ARCHITECTURE.md`, MCP Claude Code Dispatcher section): `X-Watson-Key`
header matched against `MCP_DISPATCH_API_KEY` in `.env` (the same
shared-secret pattern used by Writing Room/bodyrec), OR a bearer token from
the OAuth shim. Used the `X-Watson-Key` path for a direct local check:

```
$ curl -sS -X POST http://localhost:5200/mcp/devdispatch \
    -H "Content-Type: application/json" \
    -H "X-Watson-Key: <MCP_DISPATCH_API_KEY>" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

HTTP 200

{"id":1,"jsonrpc":"2.0","result":{"tools":[
  {
    "name": "dispatch_claude_code_job",
    "description": "Dispatch a headless Claude Code build job against a Watson-ecosystem repo. Runs on a feature branch only (never main); opens a PR when done. Never restarts services or deploys — Bill reviews, merges, pulls, and restarts manually.",
    "inputSchema": {
      "type": "object",
      "required": ["spec", "repo"],
      "properties": {
        "spec": {"type": "string", "description": "The build spec / instructions for Claude Code."},
        "repo": {"type": "string", "enum": ["watson","wcky","watson-admin","watson-ui","fms","bodyrec"], "description": "Which Watson-ecosystem repo to build against."},
        "branch_name": {"type": "string", "description": "Optional feature branch name. Auto-generated if omitted. Must not be main/master."}
      }
    }
  },
  {
    "name": "check_claude_code_job",
    "description": "Check the status of a previously dispatched Claude Code job.",
    "inputSchema": {
      "type": "object",
      "required": ["job_id"],
      "properties": {
        "job_id": {"type": "integer", "description": "The job_id returned by dispatch_claude_code_job."}
      }
    }
  }
]}}
```

Exactly 2 tools, byte-for-byte consistent with Findings 1–2. The live HTTP
surface Claude.ai's connector actually talks to is not lying, out of sync,
or serving a cached/stale response — it's correctly reflecting what's
deployed.

---

## Finding 6 — Other `merge_claude_code_job` references in the repo

```
$ grep -rln "merge_claude_code_job" . --exclude-dir=.git
.claude/worktrees/devdispatch+20260806-031322/memory/WATSON_ARCHITECTURE.md
.claude/worktrees/devdispatch+20260806-031322/jobs/devdispatch/api.py
.claude/worktrees/devdispatch+20260806-031322/jobs/devdispatch/schema.py
.claude/worktrees/devdispatch+20260806-031322/MCP-Claude-Code-Dispatcher-Spec.md
.claude/worktrees/devdispatch+20260806-032634/MCP-Claude-Code-Dispatcher-Spec.md
.claude/worktrees/devdispatch+20260806-032634/jobs/devdispatch/schema.py
.claude/worktrees/devdispatch+20260806-032634/jobs/devdispatch/api.py
.claude/worktrees/devdispatch+20260806-032634/memory/WATSON_ARCHITECTURE.md
```

No OpenAPI spec, cached schema JSON, or separate manifest anywhere else in
the tracked repo references `merge_claude_code_job` — there is no second
registration path that PR #12 (or anything else) failed to update. All
matches are inside two **other git worktree checkouts** already present on
disk under `.claude/worktrees/`:

- `worktree-devdispatch+20260806-031322` — contains the "real" implementation
  commit chain (`4dfaf09` → `8328caa` → `7ade0f4`), plus a later merge of
  `main` into it and a progress.json conflict resolution on top
  (`d0a6dde`, `71881ac`). Pushed to `origin/worktree-devdispatch+20260806-031322`.
- `worktree-devdispatch+20260806-032634` — an **independent second copy** of
  the same feature (same tool name, same file shape), branched from the
  same point. Also pushed to `origin/worktree-devdispatch+20260806-032634`.

Both branches exist on `origin` right now (`git ls-remote --heads origin`
lists both). Neither has been merged into `main` via a merge commit or a
closed PR. This looks like two separate devdispatch job runs independently
re-implementing the same requested feature, with neither one's PR actually
getting merged — Bill's "PR #12" merge was a different, coincidentally
concurrent PR from the backup-path fix track.

---

## Recommendation (not actioned — read-only investigation)

1. Diff `worktree-devdispatch+20260806-031322` vs
   `worktree-devdispatch+20260806-032634` to confirm which (if either)
   should actually ship — they may not be identical (031322 also carries the
   progress.json merge-conflict resolution on top).
2. Open/merge a real PR from whichever branch is chosen into `main`, `git
   pull` on the Beelink, restart `watson-dashboard.service`.
3. Once `main` genuinely contains `merge_claude_code_job`, re-run Finding 2
   and Finding 5's checks — at that point, and only then, if the connector
   *still* shows 2 tools, that would be real evidence of Claude.ai-side
   caching worth escalating. Right now there's no such evidence — the
   3rd tool has simply never been merged.
4. The duplicate-branch situation (two independent devdispatch runs
   producing the same feature, neither merged) may be worth a `bug_tracker`
   entry in its own right, separate from this tool-visibility question.
