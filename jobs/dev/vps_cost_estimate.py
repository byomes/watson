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

Pricing snapshot: Hetzner Cloud, Germany/Finland region, as of 2026-09-08
(post the April/June 2026 CPX/CCX price increases -- CPX in particular
roughly doubled that round, so CX is now the cheap tier, not CPX). Prices
are NOT fetched live -- re-verify against hetzner.com/cloud before treating
this as a firm budget number, and update HETZNER_PLANS/HETZNER_PRICING_ASOF
if they've changed again. Excludes VAT and the ~EUR0.50/mo IPv4 addon.

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
import math
from datetime import datetime, timezone

import psutil

from core.database import get_connection

HETZNER_PRICING_ASOF = "2026-09-08"
EUR_TO_USD = 1.16  # spot rate 2026-09-08; re-check before relying on this

# Sorted ascending by monthly_eur so recommend_plan() can return the first
# fit. CX = cost-optimized (cheapest shared vCPU tier post the 2026 CPX
# price hikes); CPX = regular-performance shared vCPU; CCX = dedicated
# vCPU. All three included so a fit is found even for unusually high
# CPU/RAM/disk requirements.
HETZNER_PLANS = [
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
HETZNER_PLANS.sort(key=lambda p: p["monthly_eur"])

CPU_HEADROOM = 1.3   # 30% above observed peak core-equivalents
MEM_HEADROOM = 1.2   # 20% above observed peak RAM
DISK_HEADROOM = 1.3  # 30% above current disk used

BEELINK_RAM_GB = 32  # from WATSON_ARCHITECTURE.md Hardware section; static, doesn't change week to week


def _cost(plan: dict | None) -> dict | None:
    if plan is None:
        return None
    monthly_usd = round(plan["monthly_eur"] * EUR_TO_USD, 2)
    return {
        **plan,
        "monthly_usd": monthly_usd,
        "daily_usd": round(monthly_usd / 30, 2),
        "daily_eur": round(plan["monthly_eur"] / 30, 2),
    }


def recommend_plan(required_vcpu: int, required_ram_gb: float, required_disk_gb: float) -> dict | None:
    for plan in HETZNER_PLANS:
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

    recommended = recommend_plan(required_vcpu, required_ram_gb, required_disk_gb)
    beelink_match = recommend_plan(total_cores, BEELINK_RAM_GB, required_disk_gb)

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
        "recommended_plan": _cost(recommended),
        "beelink_match_plan": _cost(beelink_match),
        "daily": [dict(r) for r in daily_rows],
        "pricing_asof": HETZNER_PRICING_ASOF,
        "eur_usd_rate": EUR_TO_USD,
        "headroom": {"cpu": CPU_HEADROOM, "mem": MEM_HEADROOM, "disk": DISK_HEADROOM},
    }


if __name__ == "__main__":
    import json
    print(json.dumps(build_estimate(), indent=2, default=str))
