"""jobs/dev/vps_cost_estimate.py -- multi-provider VPS cost estimate from resource_samples.

Reads jobs/dev/resource_sampler.py's resource_samples table (5-min samples,
running continuously since 2026-09-05) and maps observed CPU/RAM/disk usage
to the cheapest comparable plan at each of several VPS providers, then
averages across them. This is the "later step" flagged in
jobs/dev/weekly_utilization_report.py's docstring -- that job deliberately
reports raw numbers only; this module is where the tier/price mapping
happens now that resource_samples has real data to estimate from.

Feeds the dashboard's Dev > Cost sub-tab (GET /api/dev/vps-cost-estimate).
Read-only, on-demand -- no cron entry, no new sampling.

Originally scoped to Hetzner-only via their Cloud API, but that API
requires an account token and Hetzner requires a credit card just to
create an account -- a real barrier, not just an inconvenience. Rescoped
2026-09-09 to average across multiple providers instead of trusting one:

  - Vultr and Linode expose their full plan/pricing catalog through a
    genuinely PUBLIC, unauthenticated JSON API (api.vultr.com/v2/plans,
    api.linode.com/v4/linode/types) -- no account, no token, no card,
    fetched live on every cache-miss.
  - Hetzner and DigitalOcean don't offer that, so they're included as
    dated REFERENCE snapshots -- researched from their public pricing
    pages (no login needed to view), each carrying its own "as of" date.
    Re-verify and update HETZNER_REFERENCE_PLANS_EUR /
    DIGITALOCEAN_REFERENCE_PLANS periodically; there's no automatic
    staleness detection for these two.

Every plan pick in the response carries its own source (live / cached /
cached-stale / reference) and as-of date -- the average is never
presented as more "live" than its least-live ingredient actually is.

Live provider fetches are cached to data/dev/vps_pricing_cache.json for
PRICING_CACHE_TTL_HOURS to avoid hitting Vultr/Linode on every dashboard
load. A live-fetch failure falls back to the on-disk cache regardless of
age; if a provider has never been fetched successfully, it's simply
dropped from that response's average rather than blocking the others.
EUR->USD (for the Hetzner reference numbers) is fetched live from
api.frankfurter.app (ECB reference rate, no key required) with the same
cache/fallback behavior.

Two numbers are reported, not one:
  - "recommended" -- cheapest plan covering actually-observed peak usage
    (with headroom), averaged across providers. Answers "what would it
    cost to run what Watson actually does".
  - "beelink_match" -- cheapest plan matching the Beelink's full physical
    specs (12 threads / 32GB), averaged across providers. The ceiling,
    useful if usage is expected to grow toward the hardware's capacity.

Caveat that matters here specifically: Watson's Ollama inference is 100%
CPU-bound (see WATSON_ARCHITECTURE.md's FMSPC/LLM Stack notes -- no GPU,
NUM_PARALLEL=1 because concurrent generate calls contend for the same
cores). A shared vCPU at any of these providers is not performance-
equivalent to a physical i5-1235U thread -- matching vCPU *count* does
not guarantee matching Ollama latency. This module sizes for capacity,
not for guaranteed inference speed. Also excludes VAT/tax and bandwidth
overage charges at all providers.
"""
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
import requests

from core.database import get_connection

log = logging.getLogger(__name__)

WATSON_DIR = Path(__file__).resolve().parents[2]
PRICING_CACHE_PATH = WATSON_DIR / "data" / "dev" / "vps_pricing_cache.json"
PRICING_CACHE_TTL_HOURS = 24

VULTR_API_URL = "https://api.vultr.com/v2/plans"
LINODE_API_URL = "https://api.linode.com/v4/linode/types"
EXCHANGE_RATE_URL = "https://api.frankfurter.app/latest?from=EUR&to=USD"
FALLBACK_EUR_TO_USD = 1.16

# Hetzner Cloud, cost-optimized (CX) + regular-performance (CPX) + dedicated
# (CCX) series -- researched from hetzner.com/cloud (public pricing page,
# no login required), post the April/June 2026 CPX/CCX price increases.
HETZNER_REFERENCE_ASOF = "2026-09-08"
HETZNER_REFERENCE_PLANS_EUR = [
    {"name": "CX23", "vcpu": 2,  "ram_gb": 4,  "disk_gb": 40,  "monthly_eur": 5.49},
    {"name": "CX33", "vcpu": 4,  "ram_gb": 8,  "disk_gb": 80,  "monthly_eur": 8.49},
    {"name": "CX43", "vcpu": 8,  "ram_gb": 16, "disk_gb": 160, "monthly_eur": 15.99},
    {"name": "CX53", "vcpu": 16, "ram_gb": 32, "disk_gb": 320, "monthly_eur": 29.49},
    {"name": "CPX22", "vcpu": 2,  "ram_gb": 4,  "disk_gb": 80,  "monthly_eur": 19.49},
    {"name": "CPX32", "vcpu": 4,  "ram_gb": 8,  "disk_gb": 160, "monthly_eur": 35.49},
    {"name": "CPX42", "vcpu": 8,  "ram_gb": 16, "disk_gb": 320, "monthly_eur": 69.49},
    {"name": "CPX52", "vcpu": 12, "ram_gb": 24, "disk_gb": 480, "monthly_eur": 100.49},
    {"name": "CPX62", "vcpu": 16, "ram_gb": 32, "disk_gb": 640, "monthly_eur": 129.99},
    {"name": "CCX13", "vcpu": 2,  "ram_gb": 8,  "disk_gb": 80,  "monthly_eur": 42.99},
    {"name": "CCX23", "vcpu": 4,  "ram_gb": 16, "disk_gb": 160, "monthly_eur": 85.99},
    {"name": "CCX33", "vcpu": 8,  "ram_gb": 32, "disk_gb": 240, "monthly_eur": 138.49},
    {"name": "CCX43", "vcpu": 16, "ram_gb": 64, "disk_gb": 360, "monthly_eur": 275.99},
]

# DigitalOcean Basic (shared-CPU, standard SSD) Droplets -- researched from
# digitalocean.com/pricing/droplets (public pricing page, no login required).
DIGITALOCEAN_REFERENCE_ASOF = "2026-09-09"
DIGITALOCEAN_REFERENCE_PLANS = [
    {"name": "Basic 1vCPU/0.5GB", "vcpu": 1, "ram_gb": 0.5, "disk_gb": 10,  "monthly_usd": 4.00},
    {"name": "Basic 1vCPU/1GB",   "vcpu": 1, "ram_gb": 1,   "disk_gb": 25,  "monthly_usd": 6.00},
    {"name": "Basic 1vCPU/2GB",   "vcpu": 1, "ram_gb": 2,   "disk_gb": 50,  "monthly_usd": 12.00},
    {"name": "Basic 2vCPU/2GB",   "vcpu": 2, "ram_gb": 2,   "disk_gb": 60,  "monthly_usd": 18.00},
    {"name": "Basic 2vCPU/4GB",   "vcpu": 2, "ram_gb": 4,   "disk_gb": 80,  "monthly_usd": 24.00},
    {"name": "Basic 4vCPU/8GB",   "vcpu": 4, "ram_gb": 8,   "disk_gb": 160, "monthly_usd": 48.00},
    {"name": "Basic 8vCPU/16GB",  "vcpu": 8, "ram_gb": 16,  "disk_gb": 320, "monthly_usd": 96.00},
]

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


def _fetch_vultr_plans_live() -> list[dict]:
    resp = requests.get(VULTR_API_URL, timeout=15)
    resp.raise_for_status()
    plans = []
    for p in resp.json()["plans"]:
        if p.get("type") != "vc2" or not p.get("monthly_cost"):
            continue  # vc2 = regular shared-vCPU line; skip $0 free-tier entries
        plans.append({
            "name": p["id"],
            "vcpu": p["vcpu_count"],
            "ram_gb": round(p["ram"] / 1024, 2),
            "disk_gb": p["disk"],
            "monthly_usd": float(p["monthly_cost"]),
        })
    if not plans:
        raise RuntimeError("Vultr API returned no vc2 plans")
    plans.sort(key=lambda p: p["monthly_usd"])
    return plans


def _fetch_linode_plans_live() -> list[dict]:
    resp = requests.get(LINODE_API_URL, timeout=15)
    resp.raise_for_status()
    plans = []
    for t in resp.json()["data"]:
        if t.get("class") not in ("nanode", "standard"):
            continue  # regular shared-vCPU line; skip dedicated/highmem/gpu/premium
        monthly = (t.get("price") or {}).get("monthly")
        if not monthly:
            continue
        plans.append({
            "name": t["id"],
            "vcpu": t["vcpus"],
            "ram_gb": round(t["memory"] / 1024, 2),
            "disk_gb": round(t["disk"] / 1024, 1),
            "monthly_usd": float(monthly),
        })
    if not plans:
        raise RuntimeError("Linode API returned no nanode/standard plans")
    plans.sort(key=lambda p: p["monthly_usd"])
    return plans


def _load_cache() -> dict:
    try:
        with open(PRICING_CACHE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    PRICING_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PRICING_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


def _get_live_provider_plans(cache: dict, key: str, fetch_fn) -> tuple[list[dict] | None, str | None, str]:
    """Returns (plans, as_of_iso, source) where source is live/cached/cached-stale/unavailable."""
    entry = cache.get(key)
    if entry:
        cached_at = datetime.fromisoformat(entry["fetched_at"])
        if datetime.now(timezone.utc) - cached_at < timedelta(hours=PRICING_CACHE_TTL_HOURS):
            return entry["plans"], entry["fetched_at"], "cached"
    try:
        plans = fetch_fn()
        fetched_at = datetime.now(timezone.utc).isoformat()
        cache[key] = {"plans": plans, "fetched_at": fetched_at}
        return plans, fetched_at, "live"
    except Exception as exc:
        log.warning("%s live pricing fetch failed: %s", key, exc)
        if entry:
            return entry["plans"], entry["fetched_at"], "cached-stale"
        return None, None, "unavailable"


def recommend_plan(plans: list[dict], required_vcpu: int, required_ram_gb: float, required_disk_gb: float) -> dict | None:
    for plan in plans:  # plans must already be sorted ascending by monthly_usd
        if (plan["vcpu"] >= required_vcpu
                and plan["ram_gb"] >= required_ram_gb
                and plan["disk_gb"] >= required_disk_gb):
            return plan
    return None


def _aggregate(providers: dict, required_vcpu: int, required_ram_gb: float, required_disk_gb: float) -> dict:
    """providers: {name: (plans_or_None, {"source": ..., "asof": ...})}"""
    picks = []
    for name, (plans, meta) in providers.items():
        plan = recommend_plan(plans, required_vcpu, required_ram_gb, required_disk_gb) if plans else None
        picks.append({"provider": name, "plan": plan, **meta})

    matched = [p for p in picks if p["plan"]]
    costs = [p["plan"]["monthly_usd"] for p in matched]
    avg = round(sum(costs) / len(costs), 2) if costs else None
    return {
        "average_monthly_usd": avg,
        "average_daily_usd": round(avg / 30, 2) if avg is not None else None,
        "min_monthly_usd": round(min(costs), 2) if costs else None,
        "max_monthly_usd": round(max(costs), 2) if costs else None,
        "provider_count": len(matched),
        "providers": picks,
    }


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

    cache = _load_cache()
    vultr_plans, vultr_asof, vultr_source = _get_live_provider_plans(cache, "vultr", _fetch_vultr_plans_live)
    linode_plans, linode_asof, linode_source = _get_live_provider_plans(cache, "linode", _fetch_linode_plans_live)

    eur_usd_rate = _fetch_live_eur_usd_rate()
    if eur_usd_rate is not None:
        cache["_eur_usd_rate"] = eur_usd_rate
    else:
        eur_usd_rate = cache.get("_eur_usd_rate", FALLBACK_EUR_TO_USD)
    _save_cache(cache)

    hetzner_plans = sorted(
        [{**{k: p[k] for k in ("name", "vcpu", "ram_gb", "disk_gb")},
          "monthly_usd": round(p["monthly_eur"] * eur_usd_rate, 2)}
         for p in HETZNER_REFERENCE_PLANS_EUR],
        key=lambda p: p["monthly_usd"],
    )
    digitalocean_plans = sorted(DIGITALOCEAN_REFERENCE_PLANS, key=lambda p: p["monthly_usd"])

    providers = {
        "vultr": (vultr_plans, {"source": vultr_source, "asof": vultr_asof}),
        "linode": (linode_plans, {"source": linode_source, "asof": linode_asof}),
        "hetzner": (hetzner_plans, {"source": "reference", "asof": HETZNER_REFERENCE_ASOF}),
        "digitalocean": (digitalocean_plans, {"source": "reference", "asof": DIGITALOCEAN_REFERENCE_ASOF}),
    }

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

    # Per-day "what this would have cost on a VPS" -- same sizing/headroom
    # logic as the overall estimate above, but sized off each day's own
    # peak instead of the whole window's. This is the number Bill actually
    # wants to see day to day: what the Beelink saved him vs. renting.
    daily_with_cost = []
    cumulative_savings_usd = 0.0
    for row in daily_rows:
        d = dict(row)
        day_vcpu = max(1, math.ceil((d["peak_cpu"] / 100) * total_cores * CPU_HEADROOM))
        day_ram_gb = round(d["peak_mem"] * MEM_HEADROOM, 1)
        day_disk_gb = round(d["disk_used"] * DISK_HEADROOM, 1)
        day_agg = _aggregate(providers, day_vcpu, day_ram_gb, day_disk_gb)
        d["estimated_vps_daily_usd"] = day_agg["average_daily_usd"]
        if day_agg["average_daily_usd"] is not None:
            cumulative_savings_usd += day_agg["average_daily_usd"]
        daily_with_cost.append(d)

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
        "recommended": _aggregate(providers, required_vcpu, required_ram_gb, required_disk_gb),
        "beelink_match": _aggregate(providers, total_cores, BEELINK_RAM_GB, required_disk_gb),
        "daily": daily_with_cost,
        "savings_to_date": {
            "total_usd": round(cumulative_savings_usd, 2),
            "since": daily_rows[0]["day"] if daily_rows else None,
            "days_counted": len(daily_with_cost),
        },
        "eur_usd_rate": eur_usd_rate,
        "headroom": {"cpu": CPU_HEADROOM, "mem": MEM_HEADROOM, "disk": DISK_HEADROOM},
    }


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(build_estimate(), indent=2, default=str))
