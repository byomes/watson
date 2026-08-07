"""Persistent QC data store (reports/qc_data.json) + report writers.
One record per processed file, keyed by filename, overwritten whenever
that file is reprocessed — so book-level stats always reflect the current
state of processed/, not a stale snapshot from the first run.
"""

import csv
import json

import config, measurements


def load_qc_data():
    if not config.QC_DATA_PATH.exists():
        return {}
    with open(config.QC_DATA_PATH) as f:
        return json.load(f)


def save_qc_data(data):
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.QC_DATA_PATH, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def upsert_record(data, filename, section_number, section_title, m, specs,
                  overall_pass, retry_attempts=None, adjustments=None,
                  narrator=None, breath_count=None, final_params=None):
    data[filename] = {
        "section_number": section_number,
        "section_title": section_title,
        "narrator": narrator,
        "measurements": m,
        "specs": [
            {"name": n, "value": v, "requirement": r, "passed": p}
            for n, v, r, p in specs
        ],
        "overall_pass": overall_pass,
        "retry_attempts": retry_attempts,
        "adjustments": adjustments or [],
        "breath_count": breath_count,
        "final_params": final_params or {},
    }
    return data


def compute_book_stats(data):
    """Book-level RMS deviation check, recomputed fresh against every
    record currently in the store — not just the file being processed."""
    rms_values = [
        rec["measurements"]["integrated_loudness_db"]
        for rec in data.values()
        if rec["measurements"].get("integrated_loudness_db") is not None
    ]
    if not rms_values:
        return {"book_average_rms": None, "deviations": {}}

    avg = sum(rms_values) / len(rms_values)
    deviations = {}
    for filename, rec in data.items():
        rms = rec["measurements"].get("integrated_loudness_db")
        if rms is None:
            continue
        delta = rms - avg
        deviations[filename] = {
            "delta_db": round(delta, 2),
            "flagged": abs(delta) > config.BOOK_RMS_DEVIATION_DB,
        }
    return {"book_average_rms": round(avg, 2), "deviations": deviations}


def write_reports(data):
    """Writes reports/qc_report.md and reports/qc_report.csv from the
    current qc_data store. FAIL rows are surfaced at the top of the
    markdown report, never buried in a column."""
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    book_stats = compute_book_stats(data)
    ordered = sorted(
        data.items(),
        key=lambda kv: (kv[1]["section_number"] is None, kv[1]["section_number"] or 0),
    )

    failures = []
    for filename, rec in ordered:
        dev = book_stats["deviations"].get(filename, {})
        if not rec["overall_pass"]:
            failed_specs = [s["name"] for s in rec["specs"] if not s["passed"]]
            failures.append(f"- **{filename}** — FAILED: {', '.join(failed_specs)}")
        if dev.get("flagged"):
            failures.append(
                f"- **{filename}** — book-level RMS deviation "
                f"{dev['delta_db']:+.2f} dB from batch average "
                f"({book_stats['book_average_rms']} dB)"
            )

    lines = ["# ACX Audiobook QC Report", ""]
    if failures:
        lines.append("## ⚠️ FAILURES — REQUIRES ATTENTION")
        lines.extend(failures)
        lines.append("")
    else:
        lines.append("## All files PASS")
        lines.append("")

    lines.append(f"Book average integrated loudness: "
                 f"{book_stats['book_average_rms']} dB "
                 f"({len(data)} file(s))")
    lines.append("")

    header = [
        "Section", "Title", "Filename", "Duration (s)", "RMS/LUFS (dB)",
        "True Peak (dBTP)", "Noise Floor (dB)", "Sample Rate", "Bitrate",
        "Channels", "Head Silence (s)", "Tail Silence (s)",
        "Retries", "Breaths", "Book Δ (dB)", "Overall",
    ]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")

    csv_rows = [header]
    for filename, rec in ordered:
        m = rec["measurements"]
        dev = book_stats["deviations"].get(filename, {})
        overall = "PASS" if rec["overall_pass"] and not dev.get("flagged") else "FAIL"
        row = [
            str(rec["section_number"]) if rec["section_number"] is not None else "?",
            rec["section_title"] or "",
            filename,
            f"{m['duration']:.1f}",
            f"{m['integrated_loudness_db']:.2f}",
            f"{m['true_peak_db']:.2f}",
            f"{m['noise_floor_db']:.2f}" if m["noise_floor_db"] is not None else "n/a",
            str(m["sample_rate"]),
            f"{round(m['bit_rate'] / 1000)}k",
            str(m["channels"]),
            f"{m['head_silence_s']:.2f}",
            f"{m['tail_silence_s']:.2f}",
            str(rec.get("retry_attempts")) if rec.get("retry_attempts") is not None else "?",
            str(rec.get("breath_count")) if rec.get("breath_count") is not None else "?",
            f"{dev.get('delta_db', 0):+.2f}" if dev else "0.00",
            overall,
        ]
        lines.append("| " + " | ".join(row) + " |")
        csv_rows.append(row)

    # --- Per-chapter detail: retry count + exactly what was adjusted --------
    lines.append("")
    lines.append("## Per-chapter detail")
    lines.append("")
    for filename, rec in ordered:
        m = rec["measurements"]
        status = "PASS" if rec["overall_pass"] else "FAIL — manual review"
        attempts = rec.get("retry_attempts")
        lines.append(f"### {filename} — {status}")
        lines.append(
            f"- Final: RMS {m['integrated_loudness_db']:.2f} dB, "
            f"true peak {m['true_peak_db']:.2f} dBTP, "
            f"noise floor {m['noise_floor_db']} dB "
            f"(ACX quietest-window method)"
        )
        lines.append(f"- Attempts: {attempts if attempts is not None else '?'}"
                     f" / narrator: {rec.get('narrator') or '?'}"
                     f" / breaths suppressed: {rec.get('breath_count')}")
        adjustments = rec.get("adjustments") or []
        if adjustments:
            lines.append("- Adjustments made:")
            for a in adjustments:
                lines.append(f"  - {a}")
        else:
            lines.append("- Adjustments made: none (passed on first attempt)"
                         if rec["overall_pass"] else "- Adjustments made: none")
        if rec.get("final_params"):
            fp = rec["final_params"]
            lines.append(f"- Final params: {fp}")
        lines.append("")

    config.QC_REPORT_MD.write_text("\n".join(lines) + "\n")
    with open(config.QC_REPORT_CSV, "w", newline="") as f:
        csv.writer(f).writerows(csv_rows)
