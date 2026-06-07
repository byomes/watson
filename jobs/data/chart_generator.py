"""jobs/data/chart_generator.py — Generate bar and line charts as PNG files."""
import logging
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXPORT_DIR = REPO / "data" / "exports"

log = logging.getLogger(__name__)


def _save_path(title: str, chart_type: str, output_path: str = None) -> str:
    if output_path:
        return output_path
    import time
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    slug = title.lower().replace(" ", "_")[:30]
    return str(EXPORT_DIR / f"{chart_type}_{slug}_{int(time.time())}.png")


def bar_chart(data: dict, title: str, output_path: str = None) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    try:
        path = _save_path(title, "bar", output_path)
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.bar(list(data.keys()), list(data.values()), color="#c9a84c")
        ax.set_title(title)
        ax.set_ylabel("Value")
        plt.tight_layout()
        plt.savefig(path, dpi=150)
        plt.close(fig)
        return path
    except Exception as exc:
        log.error("bar_chart failed: %s", exc)
        return f"Error: {exc}"


def line_chart(data: dict, title: str, output_path: str = None) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    try:
        path = _save_path(title, "line", output_path)
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(list(data.keys()), list(data.values()), color="#c9a84c", marker="o")
        ax.set_title(title)
        ax.set_ylabel("Value")
        plt.tight_layout()
        plt.savefig(path, dpi=150)
        plt.close(fig)
        return path
    except Exception as exc:
        log.error("line_chart failed: %s", exc)
        return f"Error: {exc}"


def run(message: str = None) -> str:
    return "Chart generator ready. Provide data to generate a chart."
