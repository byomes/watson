"""jobs/dev/system_monitor.py — System health: CPU, memory, disk, services."""
import logging
import subprocess
import time

log = logging.getLogger(__name__)

_SERVICES = ["watson-dashboard", "watson-bot", "watson-people"]


def get_system_stats() -> dict:
    import psutil
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    boot_time = psutil.boot_time()
    uptime_hours = round((time.time() - boot_time) / 3600, 1)
    return {
        "cpu_percent": psutil.cpu_percent(interval=1),
        "memory_percent": mem.percent,
        "memory_used_gb": round(mem.used / 1e9, 2),
        "memory_total_gb": round(mem.total / 1e9, 2),
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / 1e9, 1),
        "disk_free_gb": round(disk.free / 1e9, 1),
        "uptime_hours": uptime_hours,
    }


def get_process_stats() -> list:
    import psutil
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            procs.append(p.info)
        except Exception:
            pass
    return sorted(procs, key=lambda x: x.get("cpu_percent") or 0, reverse=True)[:5]


def check_services() -> dict:
    status = {}
    for svc in _SERVICES:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True,
            )
            status[svc] = result.stdout.strip()
        except Exception:
            status[svc] = "unknown"
    return status


def run(message: str = None) -> str:
    try:
        s = get_system_stats()
        svcs = check_services()
        svc_lines = "\n".join(
            f"  {name}: {state}" for name, state in svcs.items()
        )
        return (
            f"System Health\n"
            f"─────────────\n"
            f"CPU:     {s['cpu_percent']}%\n"
            f"Memory:  {s['memory_percent']}% ({s['memory_used_gb']} / {s['memory_total_gb']} GB)\n"
            f"Disk:    {s['disk_percent']}% used, {s['disk_free_gb']} GB free\n"
            f"Uptime:  {s['uptime_hours']} hours\n\n"
            f"Services:\n{svc_lines}"
        )
    except Exception as exc:
        log.error("system_monitor run failed: %s", exc)
        return f"System monitor error: {exc}"
