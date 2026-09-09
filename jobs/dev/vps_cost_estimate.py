"""jobs/dev/vps_cost_estimate.py -- Hetzner VPS cost estimate from resource_samples.

Reads jobs/dev/resource_sampler.py's resource_samples table (5-min samples,
running continuously since 2026-09-05) and maps observed CPU/RAM/disk usage
to the cheapest Hetzner Cloud plan that would cover it (with headroom), plus
a daily/monthly cost estimate. This is the "later step" flagged in
jobs/dev/weekly_utilization_report.py's docstring -- that job deliberately
reports raw numbers only; this module is where the tier/price mapping
happens now that resource_samples has real data to estimate from.

Feeds the dashboard's Dev > Cost sub-tab (GET /api/dev/vps-cost-estimate).
Read-only, on-demand -- no cron entry, no new sampling.

Pricing is fetched LIVE from the Hetzner Cloud API (GET /v1/server_types,
Bearer token in HETZNER_API_TOKEN -- config/settings.py, .env). That API
requires a token even for read-only pricing lookups; the token needs only
the "Read" permission in the Hetzner console (Project > Security > API
Tokens) -- no servers are ever created. EUR/USD is likewise fetched live
(api.frankfurter.app, no key required, ECB reference rates).

Live pricing is cached to data/dev/hetzner_pricing_cache.json for
PRICING_CACHE_TTL_HOURS to avoid hitting both APIs on every dashboard
load. If a live fetch fails (no token yet, network issue, Hetzner API
down), it falls back to the on-disk cache regardless of age, and only
falls back to FALLBACK_PLANS (a hardcoded 2026-09-08 snapshot) if there
has never been a successful live fetch. The response always reports
"pricing_source" (live / cached / cached-stale / fallback-snapshot) so
the dashboard can show which one it's looking at -- never silently pass
off a stale or fallback number as current. Excludes VAT and the
~EUR0.50/mo IPv4 addon.

Two numbers are reported, not one:
  - "recommended" -- cheapest plan covering actually-observed peak usage
    (with headroom). This is the number that answers "what would it cost
    to run what Watson actually does".
  - "beelink_match" -- cheapest plan matching the Beelink's full physical
    specs (12 threads / 32GB), regardless of how little of that Watson is
    using right now. This is the ceiling, useful if usage is expected to
    grow toward the hardware's actual capacity.

Caveat that matters here specifically: Watson's Ollama inference is 100%
CPU-bound (see WATSON_ARCHITECTURE.md's FMSPC/LLM Stack notes -- no GPU,
NUM_PARALLEL=1 because concurrent generate calls contend for the same
cores). A Hetzner shared-vCPU core is not performance-equivalent to a
physical i5-1235U thread -- matching vCPU *count* does not guarantee
matching Ollama latency. This module sizes for capacity, not for
guaranteed inference speed.
"""
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
import requests

from config.settings import HETZNER_API_TOKEN
from core.database import get_connection

log = logging.getLogger(__name__)

WATSON_DIR = Path(__file__).resolve().parents[2]
PRICING_CACHE_PATH = WATSON_DIR / "data" / "dev" / "hetzner_pricing_cache.json"
PRICING_CACHE_TTL_HOURS = 24

HETZNER_API_URL = "https://api.hetzner.cloud/v1/server_types"
EXCHANGE_RATE_URL = "https://api.frankfurter.app/latest?from=EUR&to=USD"
# Nuremberg -- matches the "Germany/Finland region" scope of the original
# manual snapshot this replaced. Falls back to whatever location a plan
# actually lists if nbg1 isn't offered for it.
PREFERRED_LOCATION = "nbg1"

# Only shared/dedicated x86 vCPU families are a fair comparison for
# Watson's workload -- CAX (ARM/Ampere) is excluded since Ollama's CPU
# path here isn't validated on ARM, and the Beelink itself is x86.
SERIES_BY_PREFIX = {"CX": "cost-optimized", "CPX": "regular-performance", "CCX": "dedicated-vcpu"}

# Last-resort fallback, used only if there has NEVER been a successful live
# fetch (no cache on disk either) -- e.g. HETZNER_API_TOKEN isn't set yet.
# Snapshot taken 2026-09-08, post the April/June 2026 CPX/CCX price
# increases. Kept small and always labeled "fallback-snapshot" in the
# response so it's never mistaken for a live number.
FALLBACK_PLANS = [
    {"name": "CX23", "series": "cost-optimized", "vcpu": 2,  "ram_gb": 4,   "disk_gb": 40,  "monthly_eur": 5.49},
    {"name": "CX33", "series": "cost-optimized", "vcpu": 4,  "ram_gb": 8,   "disk_gb": 80,  "monthly_eur": 8.49},
    {"name": "CPX22", "series": "regular-performance", "vcpu": 2,  "ram_gb": 4,   "disk_gb": 80,  "monthly_eur": 19.49},
    {"name": "CX43", "series": "cost-optimized", "vcpu": 8,  "ram_gb": 16,  "disk_gb": 160, "monthly_eur": 15.99},
    {"name": "CPX32", "series": "regular-performance", "vcpu": 4,  "ram_gb": 8,   "disk_gb": 160, "monthly_eur": 35.49},
    {"name": "CX53", "series": "cost-optimized", "vcpu": 16, "ram_gb": 32,  "disk_gb": 320, "monthly_eur": 29.49},
    {"name": "CCX13", "series": "dedicated-vcpu", "vcpu": 2,  "ram_gb": 8,   "disk_gb": 80,  "monthly_eur": 42.99},
    {"name": "CPX42", "series": "regular-performance", "vcpu": 8,  "ram_gb": 16,  "disk_gb": 320, "monthly_eur": 69.49},
    {"name": "CCX23", "series": "dedicated-vcpu", "vcpu": 4,  "ram_gb": 16,  "disk_gb": 160, "monthly_eur": 85.99},
    {"name": "CPX52", "series": "regular-performance", "vcpu": 12, "ram_gb": 24,  "disk_gb": 480, "monthly_eur": 100.49},
    {"name": "CPX62", "series": "regular-performance", "vcpu": 16, "ram_gb": 32,  "disk_gb": 640, "monthly_eur": 129.99},
    {"name": "CCX33", "series": "dedicated-vcpu", "vcpu": 8,  "ram_gb": 32,  "disk_gb": 240, "monthly_eur": 138.49},
    {"name": "CCX43", "series": "dedicated-vcpu", "vcpu": 16, "ram_gb": 64,  "disk_gb": 360, "monthly_eur": 275.99},
]
FALLBACK_EUR_TO_USD = 1.16
FALLBACK_ASOF = "2026-09-08"

CPU_HEADROOM = 1.3   # 30% above observed peak core-equivalents
MEM_HEADROOM = 1.2   # 20% above observed peak RAM
DISK_HEADROOM = 1.3  # 30% above current disk used

BEELINK_RAM_GB = 32  # from WATSON_ARCHITECTURE.md Hardware section; static, doesn't change week to week


def _fetch_live_eur_usd_rate() -> float | None:
    try:
        resp = requests.get(EXCHANGE_RATE_URL, timeout=10)
        resp.raise_for_status()
        return float(resp.json()["rates"]["USD"])
    except Exception as exc:
        log.warning("live EUR/USD rate fetch failed: %s", exc)
        return None


def _fetch_live_plans() -> list[dict]:
    if not HETZNER_API_TOKEN:
        raise RuntimeError("HETZNER_API_TOKEN not set -- see config/settings.py")

    resp = requests.get(
        HETZNER_API_URL,
        headers={"Authorization": f"Bearer {HETZNER_API_TOKEN}"},
        timeout=15,
    )
    resp.raise_for_status()
    server_types = resp.json()["server_types"]

    plans = []
    for st in server_types:
        name = st["name"].upper()
        prefix = next((p for p in SERIES_BY_PREFIX if name.startswith(p)), None)
        if prefix is None:
            continue  # CAX (ARM) and anything outside CX/CPX/CCX
        if st.get("architecture", "x86") != "x86":
            continue
        if st.get("deprecated") or st.get("deprecation") is not None:
            continue  # being retired -- don't recommend it

        prices = st.get("prices") or []
        price = next((p for p in prices if p.get("location") == PREFERRED_LOCATION), None)
        if price is None and prices:
            price = prices[0]
        if price is None:
            continue

        monthly = price["price_monthly"]
        plans.append({
            "name": name,
            "series": SERIES_BY_PREFIX[prefix],
            "vcpu": st["cores"],
            "ram_gb": st["memory"],
            "disk_gb": st["disk"],
            "_monthly_native": float(monthly["net"]),
            "_native_currency": monthly.get("currency", "EUR"),
            "location": price.get("location", PREFERRED_LOCATION),
        })

    if not plans:
        raise RuntimeError("Hetzner API returned no matching x86 CX/CPX/CCX plans")

    return plans


def _normalize_to_eur(plans: list[dict], eur_usd_rate: float) -> list[dict]:
    out = []
    for p in plans:
        native, currency = p.pop("_monthly_native"), p.pop("_native_currency")
        monthly_eur = native if currency == "EUR" else round(native / eur_usd_rate, 2)
        out.append({**p, "monthly_eur": monthly_eur})
    out.sort(key=lambda p: p["monthly_eur"])
    return out


def _load_cache() -> dict | None:
    try:
        with open(PRICING_CACHE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_cache(plans: list[dict], eur_usd_rate: float, fetched_at: str) -> None:
    PRICING_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PRICING_CACHE_PATH, "w") as f:
        json.dump({"plans": plans, "eur_usd_rate": eur_usd_rate, "fetched_at": fetched_at}, f, indent=2)


def _get_pricing() -> tuple[list[dict], float, str, str]:
    """Returns (plans, eur_usd_rate, pricing_asof, source).

    source is one of "live", "cached", "cached-stale", "fallback-snapshot".
    """
    cache = _load_cache()
    if cache:
        cached_at = datetime.fromisoformat(cache["fetched_at"])
        if datetime.now(timezone.utc) - cached_at < timedelta(hours=PRICING_CACHE_TTL_HOURS):
            return cache["plans"], cache["eur_usd_rate"], cache["fetched_at"], "cached"

    try:
        raw_plans = _fetch_live_plans()
        eur_usd_rate = _fetch_live_eur_usd_rate() or (cache["eur_usd_rate"] if cache else FALLBACK_EUR_TO_USD)
        plans = _normalize_to_eur(raw_plans, eur_usd_rate)
        fetched_at = datetime.now(timezone.utc).isoformat()
        _save_cache(plans, eur_usd_rate, fetched_at)
        return plans, eur_usd_rate, fetched_at, "live"
    except Exception as exc:
        log.warning("live Hetzner pricing fetch failed, falling back: %s", exc)
        if cache:
            return cache["plans"], cache["eur_usd_rate"], cache["fetched_at"], "cached-stale"
        return FALLBACK_PLANS, FALLBACK_EUR_TO_USD, FALLBACK_ASOF, "fallback-snapshot"


def _cost(plan: dict | None, eur_usd_rate: float) -> dict | None:
    if plan is None:
        return None
    monthly_usd = round(plan["monthly_eur"] * eur_usd_rate, 2)
    return {
        **plan,
        "monthly_usd": monthly_usd,
        "daily_usd": round(monthly_usd / 30, 2),
        "daily_eur": round(plan["monthly_eur"] / 30, 2),
    }


def recommend_plan(plans: list[dict], required_vcpu: int, required_ram_gb: float, required_disk_gb: float) -> dict | None:
    for plan in plans:
        if (plan["vcpu"] >= required_vcpu
                and plan["ram_gb"] >= required_ram_gb
                and plan["disk_gb"] >= required_disk_gb):
            return plan
    return None


def _fetch_samples(conn, window_days: int):
    return conn.execute(
        """SELECT sampled_at, cpu_percent, mem_used_gb, mem_total_gb, disk_used_gb
           FROM resource_samples
           WHERE sampled_at >= datetime('now', ?)
           ORDER BY sampled_at ASC""",
        (f"-{window_days} days",),
    ).fetchall()


def _fetch_daily_rows(conn, days: int):
    return conn.execute(
        """SELECT date(sampled_at) AS day,
                  AVG(cpu_percent) AS avg_cpu, MAX(cpu_percent) AS peak_cpu,
                  AVG(mem_used_gb) AS avg_mem, MAX(mem_used_gb) AS peak_mem,
                  MAX(mem_total_gb) AS mem_total,
                  MAX(disk_used_gb) AS disk_used
           FROM resource_samples
           WHERE sampled_at >= datetime('now', ?)
           GROUP BY day
           ORDER BY day ASC""",
        (f"-{days} days",),
    ).fetchall()


def build_estimate(sizing_window_days: int = 7, daily_rows_days: int = 14) -> dict:
    with get_connection() as conn:
        samples = _fetch_samples(conn, sizing_window_days)
        daily_rows = _fetch_daily_rows(conn, daily_rows_days)

    if not samples:
        return {
            "available": False,
            "reason": "No resource_samples yet -- jobs/dev/resource_sampler.py "
                      "runs every 5 min; check crontab / logs/resource_sampler.log.",
        }

    plans, eur_usd_rate, pricing_asof, pricing_source = _get_pricing()

    n = len(samples)
    peak_cpu_percent = max(s["cpu_percent"] for s in samples)
    avg_cpu_percent = sum(s["cpu_percent"] for s in samples) / n
    peak_mem_gb = max(s["mem_used_gb"] for s in samples)
    avg_mem_gb = sum(s["mem_used_gb"] for s in samples) / n
    disk_used_gb = samples[-1]["disk_used_gb"]
    total_cores = psutil.cpu_count(logical=True) or 12

    first_sample_at = samples[0]["sampled_at"]
    try:
        span_hours = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(first_sample_at.replace(" ", "T") + "+00:00")
        ).total_seconds() / 3600
    except ValueError:
        span_hours = None

    required_vcpu = max(1, math.ceil((peak_cpu_percent / 100) * total_cores * CPU_HEADROOM))
    required_ram_gb = round(peak_mem_gb * MEM_HEADROOM, 1)
    required_disk_gb = round(disk_used_gb * DISK_HEADROOM, 1)

    recommended = recommend_plan(plans, required_vcpu, required_ram_gb, required_disk_gb)
    beelink_match = recommend_plan(plans, total_cores, BEELINK_RAM_GB, required_disk_gb)

    return {
        "available": True,
        "sizing_window_days": sizing_window_days,
        "sample_count": n,
        "data_span_hours": round(span_hours, 1) if span_hours is not None else None,
        "usage": {
            "total_cores": total_cores,
            "peak_cpu_percent": round(peak_cpu_percent, 1),
            "avg_cpu_percent": round(avg_cpu_percent, 1),
            "peak_mem_gb": round(peak_mem_gb, 2),
            "avg_mem_gb": round(avg_mem_gb, 2),
            "disk_used_gb": round(disk_used_gb, 1),
        },
        "required": {
            "vcpu": required_vcpu,
            "ram_gb": required_ram_gb,
            "disk_gb": required_disk_gb,
        },
        "recommended_plan": _cost(recommended, eur_usd_rate),
        "beelink_match_plan": _cost(beelink_match, eur_usd_rate),
        "daily": [dict(r) for r in daily_rows],
        "pricing_asof": pricing_asof,
        "pricing_source": pricing_source,
        "eur_usd_rate": eur_usd_rate,
        "headroom": {"cpu": CPU_HEADROOM, "mem": MEM_HEADROOM, "disk": DISK_HEADROOM},
    }


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(build_estimate(), indent=2, default=str))
