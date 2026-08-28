# wtsn.me — Public-Facing Tools Domain: Investigation Spec

*Investigation only. No code, repos, Vercel changes, or DNS changes made. All findings below are from live checks against the real systems, not from architecture-doc assumptions, on 2026-08-27.*

---

## 1. Current state (factual findings)

### Domain / DNS
- **wtsn.me nameservers:** `dns1.registrar-servers.com` / `dns2.registrar-servers.com` — Namecheap's own default (BasicDNS) nameservers. **Not delegated anywhere.**
- **wtsn.me is not idle-parked** — it's actively running Namecheap's URL Forwarding service right now: `curl -I http://wtsn.me` returns `302 → http://www.wtsn.me/`, header `X-Served-By: Namecheap URL Forward`. Something (Bill, at purchase, likely via a default Namecheap setup prompt) already created a forwarding record. This will need to be removed/overridden regardless of which architecture below is chosen.
- **A record:** `162.255.119.168` — Namecheap's own parking/forwarding IP, consistent with the above.
- **Namecheap API:** No `NAMECHEAP_API_*` credentials anywhere in `~/watson/.env` (full var list checked). No evidence of API being enabled on the account, no IP-whitelisting groundwork. This would be built from zero if pursued.
- **Beelink public IP:** IPv4 `96.245.229.112` (residential, presumably non-static — not verified as static/dynamic over time in this session; would need to be confirmed or handled with a dyndns-style updater if the Beelink itself ever needed to be an IP-whitelisted Namecheap API caller).

### Vercel account state
- **Existing projects** (`vercel project ls`, account `billyomes-2015s-projects`): `watson`, `wcky`, `watson-people`, `comms-desk`, `curator`, `bodyrec`, `watson-admin`, `fms`, `watson-ui`, and one unrecognized (`dba`, 100d old — worth asking Bill about, not part of this investigation's scope). All auto-deploy on push per the architecture doc.
- **Existing custom domains** (`vercel domains ls`): only `williamckyomes.com` and `faithmakessense.com`, both listed as **"Third Party" registrar and "Third Party" nameservers** — meaning Vercel is *not* authoritative DNS for either. `dig williamckyomes.com NS` confirms: `ns1.startlogic.com` / `ns2.startlogic.com` (a legacy host, not even Namecheap). This is important precedent: **every domain Bill has connected to Vercel so far used per-record delegation (Option B's DNS shape, §2), not nameserver delegation (Option A's DNS shape) — done manually, one-time, at whatever registrar/DNS host each domain lives at.** There is no existing example of nameserver delegation to Vercel in this account to point to as precedent either way.
- **Vercel API token:** No `VERCEL_TOKEN`/`VERCEL_API_TOKEN` in `.env`. What *does* exist:
  - `VERCEL_DEPLOY_HOOK` — a deploy-hook URL scoped to one specific existing project/hook, not a general API credential. Can't create projects or assign domains.
  - **Undocumented but present:** `~/.local/share/com.vercel.cli/auth.json` holds a live personal Vercel CLI session (`token`, `refreshToken`, `expiresAt`, `userId`) from Bill having run `vercel login` interactively at some point. This is real, working credential — `vercel project ls`/`vercel domains ls` both worked against it live during this investigation. **But this is Bill's personal browser-authenticated CLI session, not a token minted for automation** — reusing it inside an unattended Watson job would mean an automated process holding the same access as Bill's own logged-in account, with no separate scope, no separate revocation path, and an expiry that isn't managed for that purpose. A proper Vercel **Access Token** (`vercel.com/account/tokens`), scoped and named for this purpose, should be minted fresh rather than reusing this file.
- **Plan/tier:** Not directly confirmed via CLI in this session (no `vercel teams ls`/billing check run — team name reads as `billyomes-2015s-projects`, consistent with a personal/Hobby-tier account naming pattern, but this is inference, not a confirmed fact). Flag as a fact to confirm before committing to an architecture that depends on tier limits (§4 open question).

### GitHub
- `GITHUB_TOKEN` in `.env` carries very broad scopes: `admin:org`, `admin:repo_hook`, `delete_repo`, `repo`, `workflow`, etc. — **this token can create new repos in the `byomes` org today.** (`gh auth status` confirms the same token, logged in as `byomes`.) So Option B's repo-creation step (§3) is technically unblocked by credentials — the open question is whether handing an automated job repo-creation + delete-capable scope is a blast-radius increase worth taking versus this token's current, narrower actual usage (push/PR only, per the dispatcher code).

### MCP Claude Code Dispatcher — confirmed against live code, not just the doc
Read `jobs/devdispatch/api.py` directly (not just `WATSON_ARCHITECTURE.md`'s summary):
- `ALLOWED_REPOS = ("watson", "wcky", "watson-admin", "watson-ui", "fms", "bodyrec")` — fixed tuple, `wtsn.me`/a new tools repo is not in it and nothing in the code reads this list from anywhere dynamic.
- No Vercel API calls anywhere in the file (`grep -i vercel` → zero hits).
- No GitHub repo-*creation* calls — only branch push + PR-open against an existing repo (`_open_pr()` uses `PyGithub` against a repo already cloned locally; no `create_repo` call exists).
- **The doc's claim is accurate:** the dispatcher creates a worktree branch, pushes, opens a PR. It does not create repos, does not call Vercel, does not touch DNS, in any form, today.

---

## 2. Recommended DNS architecture

**Recommendation: Option A — nameserver delegation to Vercel.**

Reasoning:
- The stated goal is "create slugs at will, without coming back to me each time." Only nameserver delegation actually removes Namecheap from every future request — once delegated, every future subdomain or record is a pure Vercel API/dashboard action, zero Namecheap calls, zero second credential to keep alive.
- Option B (keep DNS at Namecheap, automate per-record creation via Namecheap API) requires building a whole second automation surface from scratch for this one domain — no `NAMECHEAP_API_*` credentials exist, API access isn't enabled on the account yet, and Namecheap's API requires per-calling-machine IP whitelisting, which is a real ongoing maintenance cost if the Beelink's IP isn't static (not confirmed either way this session). This is strictly more moving parts for less capability than Option A.
- Vercel's nameservers do support arbitrary record types (MX, TXT, etc.) via their DNS management UI/API — so delegating doesn't box Bill out of eventually wanting email or other records on `wtsn.me`. This isn't a real limitation, just worth saying explicitly rather than assuming.
- The one-time manual cost (changing NS records at Namecheap) is small and matches the phased-build pattern (§5) — a single keyboard moment for Bill, then fully unattended after.

**Caveat surfaced by this investigation, not assumed away:** every domain currently in this Vercel account (`williamckyomes.com`, `faithmakessense.com`) uses the *other* pattern (per-record, DNS kept elsewhere). That's not evidence Option A is wrong — those domains predate this "create tools at will" requirement and have other things living on them (MX/email, an existing legacy host) that `wtsn.me` doesn't. But it does mean Option A would be a new pattern for this account, not an extension of an existing one — worth Bill's explicit sign-off before the nameserver change (see §6 open questions), not just an inference from precedent.

**Also worth naming:** `wtsn.me` currently has an active Namecheap URL Forward record (→ `www.wtsn.me`) that predates this plan. Whichever option is chosen, that forward needs to be explicitly removed as part of setup — leaving it would silently keep old traffic routing through Namecheap's forwarding service instead of the new destination.

---

## 3. Recommended repo/deploy architecture

**Recommendation: Option A — one shared "Watson Public Tools" Next.js app / one Vercel project / one repo, tools as routes.**

Reasoning:
- This is not a novel idea — it's the **exact pattern already live and working** for wcky: `/arc`, `/room`, `/tools/connect-card`, and especially `/go/[slug]` are all routes inside one Next.js app on one Vercel project. Adding the Catalyst connect-card-style tool that prompted this request would look identical in shape to `src/app/tools/connect-card/` today.
- Read `src/app/go/[slug]/route.ts` directly: it's a **single dynamic route**, not one file per redirect — it resolves the slug against `GET https://watson.tail0243ff.ts.net/api/links/resolve/<slug>` and 307-redirects. That means new branded links under `/go/` already require **zero deploys today** — just a new row in whatever table `api/links/resolve` reads. That's the strongest existing proof that "create at will" doesn't require Option B's per-tool repo/project/domain machinery — for anything that's fundamentally data (a redirect, a form target, a slug-keyed page), a single dynamic route + a Watson-API-backed data layer gets Bill "at will" creation with literally no Vercel/GitHub API calls at all, just a DB write.
- Option B (one repo + one Vercel project + one subdomain per tool) triples the failure surface for every single new tool: repo creation (needs the broad-scoped `GITHUB_TOKEN`, see §1), a new Vercel project link, and a new domain-assignment API call — three things that can each partially fail mid-creation, for tools like a connect-card clone that don't need isolation from each other. `bodyrec` and `comms-desk` already show the *actual* pattern Bill uses when a tool genuinely does need its own domain/repo/deploy lifecycle (its own auth model, its own release cadence) — that's a deliberate choice made per-project already, not something to default into for every new small tool.
- Isolation is a real, occasionally valid reason to split a tool into its own repo (a bug in one tool's app breaking another's is a genuine cost) — but that's answerable *after* a tool exists and turns out to need it, same as `bodyrec`/`comms-desk` presumably did. Building Option B's full automation up front, for every tool including simple ones, pays that overhead unconditionally.

**Two-tier shape this suggests**, worth putting in front of Bill rather than deciding unilaterally (see §6):
1. **Data-driven tools** (redirects, simple form-to-Watson-API submissions) — no deploy at all, just a Watson DB write through an existing or new generic route, same shape as `/go/[slug]`.
2. **Code tools** (anything with real custom UI/logic, like a connect-card clone) — a new route/page in the shared app, Claude Code commits + pushes to `main`, Vercel redeploys the one project. Still zero new Vercel/GitHub API surface — the *only* one-time setup for the whole domain is pointing `wtsn.me` at this one Vercel project once.

---

## 4. Credentials missing / needed before any build starts

| Credential | Status | Needed for |
|---|---|---|
| Vercel Access Token (scoped, named for this purpose) | **Missing** — only an undocumented personal CLI session exists (`~/.local/share/com.vercel.cli/auth.json`); do not reuse it for automation | Confirming free/current-plan project & domain limits now; any future automation that needs to call the Vercel API directly (unlikely to be needed at all under the Option A recommendation above, since the one domain→project assignment is a single one-time manual/CLI step) |
| Namecheap API key + IP whitelist | **Not needed if Option A (§2) is adopted.** Would be a from-scratch build (no groundwork exists) if Option B were chosen instead | Only relevant under the DNS option not recommended here |
| Vercel plan/tier confirmation | **Not confirmed this session** | Sanity-checking multi-domain-per-project support before relying on it (Option A's shared-project model may eventually want both `wtsn.me` and a `www.`/subdomain variant live on the same project) |
| Repo-scoped GitHub token for this specific job, vs. reusing the existing broad `GITHUB_TOKEN` | **Open question**, not a missing credential — the existing token already has `byomes`-org repo-create scope | Only relevant if a future tool genuinely needs Option B's isolation and repo creation gets built later |

**Where these would live:** per existing convention, `~/watson/.env` at runtime + `SECRETS.md` on OneDrive as the master copy. Under the Option A recommendation above, this build likely needs **zero new secrets** beyond what already exists (no new Vercel/Namecheap API credential at all — the domain→project link is a single manual/CLI step, not something an automated job calls repeatedly). This makes it a reasonable candidate to *not* wait on the still-unbuilt Infisical item — there's very little new secret surface to justify standing that up just for this.

---

## 5. Phased build plan

**Phase 0 — one-time, Bill at a keyboard (not automatable, not proposed to build this session):**
1. Remove the existing Namecheap URL Forward record on `wtsn.me`.
2. Change `wtsn.me`'s nameservers at Namecheap to Vercel's (`ns1.vercel-dns.com` / `ns2.vercel-dns.com`, confirmed via Vercel's own domain-add flow rather than guessed).
3. Add `wtsn.me` as a domain on whichever Vercel project ends up hosting these tools (new "Watson Public Tools" project if a new shared app, or the existing `wcky` project if these tools are folded into it instead — this is exactly the kind of judgment call flagged as an open question below, not decided here).
4. Confirm DNS has propagated and the domain resolves through Vercel before anything is built against it.

**Phase 1 — one-time, Claude Code/Watson, unattended (after Phase 0):**
- Scaffold the shared Next.js app (if new project chosen over folding into wcky) — same stack/pattern as wcky, following the existing repo/deploy convention (`github.com/byomes/<repo>` → Beelink path → Vercel auto-deploy on push).
- Register the new repo in `ALLOWED_REPOS` (`jobs/devdispatch/schema.py`) if it's meant to be dispatchable via the MCP dispatcher/Claude.ai flow — a small, explicit code change, not automatic.

**Phase 2 — ongoing, Claude Code/Watson, unattended, "at will" per the original ask:**
- New data-driven tool (redirect/simple form): a DB write through an existing generic route — no deploy.
- New code tool: Claude Code adds a route/page to the shared app, commits, pushes to `main` — Vercel redeploys automatically. This can go through the existing `dispatch_claude_code_job`/`merge_claude_code_job` flow unchanged once the repo is in the allow-list (§ above) — **no extension to the dispatcher's own scope is needed under the Option A recommendation**, since nothing about it needs to call Vercel or create repos. This directly answers the prompt's §5 question of "extend the dispatcher vs. build a separate job that calls it" — under Option A, there's nothing new for a separate job to orchestrate; the existing dispatcher already covers 100% of what's needed.

---

## 6. Open questions for Bill

1. **New shared app vs. folding into wcky.** wcky already has exactly this pattern (`/tools/connect-card`, `/go/`) live on `williamckyomes.com`. Does `wtsn.me` want to be a *second* domain on the wcky project (Vercel supports multiple custom domains per project), keeping one app/one place to look — or does Bill want a visibly separate "Watson Public Tools" identity/repo, on the theory that `wtsn.me` is for strangers specifically and he'd rather that surface not share a codebase with his personal/ministry site? This is a real judgment call about identity and blast-radius, not a technical constraint either way.
2. **Nameserver delegation, specifically.** §2 recommends it, but it's a new pattern for this account (no existing domain here uses it) and it does mean Vercel becomes the sole DNS authority for `wtsn.me`, including anything non-web Bill might want on it later (email, etc.) — technically unrestricted per Vercel's DNS management, but still a different operational home than Namecheap. Confirm before Phase 0 touches real DNS.
3. **First-deploy approval gate for a public-facing tool.** Every existing Claude Code dispatch ends in a PR Bill reviews before merge — but nothing currently distinguishes "this PR, once merged, becomes reachable by strangers, indexed, linkable" from any other merge. Should the *first* deploy of a genuinely new public tool (not incremental edits to an existing one) get an extra explicit Telegram confirm beyond the normal PR-merge gate, given the audience is no longer just Bill/congregation/Tailscale-network? Or is the existing PR-review gate already sufficient since Bill is the one merging either way? This is flagged, not decided.
4. **Abuse/rate-limiting posture.** Every existing public surface (connect-card, ARC signup, `/docs` KB export) either validates specific known-shape input or sits behind an unguessable/token-gated link. `wtsn.me` will be the first domain built to be openly linked and indexed with no existing traffic baseline to compare anomalies against. Worth deciding up front whether new public tools on this domain get a default rate-limit/bot-traffic posture (e.g., Vercel's own firewall/WAF features) rather than each tool inventing its own, given there's no precedent to copy yet.
5. **Vercel plan/tier.** Not confirmed this session (see §4) — worth a quick manual check before committing to the shared-project model, in case Hobby-tier custom-domain-per-project limits matter once `wtsn.me` plus any future variant (`www.wtsn.me`, a subdomain) are both wanted live simultaneously.

---

## 7. Build log (2026-08-27 / 2026-08-28)

The recommendations above were built and verified live, in a separate session — see git history in `~/watson` (`jobs/tools/`) and `~/watson-tools` for the actual implementation. Key real-world findings from the build, not anticipated by the investigation:

- **`WATSON_API_URL` had to be provisioned as a Vercel project env var** — it existed in `watson-tools/.env.example` but was never actually added to the live Vercel project, which caused a real bug (an uncaught error → `500` instead of the intended `404` for a draft tool) on first live test. Fixed by adding the var via the Vercel API and redeploying. Worth checking for on any future Vercel project built the same way — an `.env.example` documents the shape, it doesn't provision anything.
- **`phase4-test`** was a throwaway `page`-type row registered directly against the live `public_tools` table to prove the full pathway end to end (repo → Vercel deploy → domain resolution → Telegram first-deploy confirm → public reachability outside Tailscale). It went live at `wtsn.me/phase4-test` for real, confirmed working, then was **intentionally deleted** from `public_tools` once the test passed — noted here so a later audit of `public_tools` or of `wtsn.me/phase4-test` traffic in logs isn't left wondering what it was or why it briefly existed.
- **`www.wtsn.me` → `wtsn.me` canonical redirect** was added at the Vercel domain level (not app code) after the initial build left both resolving independently — see the Web Properties table in `WATSON_ARCHITECTURE.md` for the live state.
