"""Passive watchdog for stuck/orphaned Ollama runner processes (bug #28).

Ollama runs one llama-server child process per loaded model. Under CPU
contention (this box has no GPU — every model runs on shared cores), a
generation can take far longer than any client waited for, tying up that
model's single execution slot (OLLAMA_NUM_PARALLEL=1) for minutes and
starving unrelated requests (e.g. the Telegram intent classifier).

This does not kill anything — it only alerts, since a false positive
(flagging a real long-running job) is worse than a missed one here.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from core.vacation import vacation_gate  # noqa: E402

MANIFESTS_DIR = Path("/usr/share/ollama/.ollama/models/manifests/registry.ollama.ai/library")
CPU_PCT_THRESHOLD = 15.0
SAMPLE_INTERVAL_SECONDS = 2


def _model_name_for_blob(blob_hash: str) -> str:
    if not MANIFESTS_DIR.is_dir():
        return "unknown"
    for f in MANIFESTS_DIR.rglob("*"):
        if not f.is_file():
            continue
        try:
            if blob_hash in f.read_text(errors="ignore"):
                return f"{f.parent.name}:{f.name}"
        except OSError:
            continue
    return "unknown"


def _llama_server_procs() -> list[dict]:
    out = subprocess.run(
        ["ps", "-eo", "pid,cmd", "--no-headers"], capture_output=True, text=True, check=False
    ).stdout
    procs = []
    for line in out.splitlines():
        line = line.strip()
        if "llama-server" not in line:
            continue
        pid_str, cmd = line.split(None, 1)
        port_m = re.search(r"--port (\d+)", cmd)
        model_m = re.search(r"--model \S+/blobs/sha256-([0-9a-f]+)", cmd)
        if not port_m or not model_m:
            continue
        procs.append({"pid": int(pid_str), "port": port_m.group(1), "blob": model_m.group(1)})
    return procs


def _cpu_ticks(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # Field 2 (comm) may contain spaces/parens — split after the closing ')'.
    fields = stat[stat.rfind(")") + 2 :].split()
    utime, stime = int(fields[11]), int(fields[12])
    return utime + stime


def _established_conns(port: str) -> int:
    out = subprocess.run(
        ["ss", "-tn", "state", "established", f"( sport = :{port} or dport = :{port} )"],
        capture_output=True, text=True, check=False,
    ).stdout
    lines = [l for l in out.splitlines() if l.strip() and not l.startswith("Recv-Q")]
    return len(lines)


def _telegram(text: str) -> None:
    if vacation_gate("system_failure", "jobs.dev.ollama_monitor", text):
        return
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("WATSON_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("WATSON_CHAT_ID")
    if not (bot_token and chat_id):
        print("Warning: bot token/chat id not set — skipping Telegram.", file=sys.stderr)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
    except Exception as exc:
        print(f"Telegram failed: {exc}", file=sys.stderr)


def main() -> None:
    procs = _llama_server_procs()
    if not procs:
        return

    ticks_per_sec = os.sysconf("SC_CLK_TCK")
    before = {p["pid"]: _cpu_ticks(p["pid"]) for p in procs}
    time.sleep(SAMPLE_INTERVAL_SECONDS)

    flagged = []
    for p in procs:
        after = _cpu_ticks(p["pid"])
        b = before.get(p["pid"])
        if after is None or b is None:
            continue  # process exited between samples
        cpu_pct = (after - b) / ticks_per_sec / SAMPLE_INTERVAL_SECONDS * 100
        conns = _established_conns(p["port"])
        if cpu_pct >= CPU_PCT_THRESHOLD and conns == 0:
            flagged.append({**p, "cpu_pct": cpu_pct, "model": _model_name_for_blob(p["blob"])})

    if not flagged:
        return

    lines = ["Ollama watchdog: possible stuck runner(s) — busy CPU, no active client connection."]
    for f in flagged:
        lines.append(
            f"  pid={f['pid']} model={f['model']} port={f['port']} cpu~{f['cpu_pct']:.0f}%"
        )
    lines.append("Not killed automatically — check `ollama ps` / `ps aux | grep llama-server`.")
    text = "\n".join(lines)
    print(text)
    _telegram(text)


if __name__ == "__main__":
    main()

# Cron: */5 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/dev/ollama_monitor.py >> /home/billyomes/watson/logs/ollama_monitor.log 2>&1
