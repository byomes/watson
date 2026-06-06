"""jobs/data/data_analyzer.py — Analyze CSV and Excel files using pandas."""
import logging
import re

log = logging.getLogger(__name__)

_PATH_RE = re.compile(r'[\w/~.\-]+\.(csv|xlsx?)', re.IGNORECASE)


def _format_summary(df, path: str) -> str:
    import pandas as pd
    lines = [
        f"File: {path}",
        f"Rows: {len(df):,}   Columns: {len(df.columns)}",
        f"Columns: {', '.join(str(c) for c in df.columns)}",
        "",
    ]
    try:
        stats = df.describe(include="all")
        lines.append(stats.to_string())
    except Exception:
        pass
    null_counts = df.isnull().sum()
    if null_counts.any():
        lines.append("\nMissing values:")
        for col, n in null_counts[null_counts > 0].items():
            lines.append(f"  {col}: {n}")
    return "\n".join(lines)


def analyze_csv(path: str) -> str:
    import pandas as pd
    try:
        df = pd.read_csv(path)
        return _format_summary(df, path)
    except Exception as exc:
        log.error("analyze_csv failed: %s", exc)
        return f"Error reading CSV: {exc}"


def analyze_xlsx(path: str) -> str:
    import pandas as pd
    try:
        df = pd.read_excel(path)
        return _format_summary(df, path)
    except Exception as exc:
        log.error("analyze_xlsx failed: %s", exc)
        return f"Error reading Excel: {exc}"


def run(message: str = None) -> str:
    if not message:
        return "Data analyzer ready. Provide a CSV or Excel file path."
    match = _PATH_RE.search(message)
    if not match:
        return "No CSV or Excel file path found in message."
    import os
    path = match.group(0).replace("~", os.path.expanduser("~"))
    if path.lower().endswith(".csv"):
        return analyze_csv(path)
    return analyze_xlsx(path)
