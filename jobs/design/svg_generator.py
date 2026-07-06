"""jobs/design/svg_generator.py — Generate branded banners, quote cards, and social graphics."""
import logging
import os
import re
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXPORTS_DIR = REPO / "data" / "exports"

log = logging.getLogger(__name__)

BRAND_BG = "#111827"
BRAND_GOLD = "#c9a84c"
BRAND_TEXT = "#e8eaed"


def _svg_to_png(svg_path: str, png_path: str) -> str:
    import cairosvg
    cairosvg.svg2png(url=svg_path, write_to=png_path)
    return png_path


def create_banner(title: str, subtitle: str = "", width: int = 1200, height: int = 630,
                  bg_color: str = BRAND_BG, text_color: str = BRAND_GOLD,
                  output_path: str = None) -> str:
    import svgwrite
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    svg_path = str(EXPORTS_DIR / f"banner_{ts}.svg")
    png_path = output_path or str(EXPORTS_DIR / f"banner_{ts}.png")
    try:
        dwg = svgwrite.Drawing(svg_path, size=(width, height))
        dwg.add(dwg.rect(insert=(0, 0), size=(width, height), fill=bg_color))
        # Gold accent bar
        dwg.add(dwg.rect(insert=(0, height - 8), size=(width, 8), fill=text_color))
        # Title
        title_y = height // 2 - (40 if subtitle else 0)
        dwg.add(dwg.text(title, insert=(width // 2, title_y),
                         text_anchor="middle", dominant_baseline="middle",
                         fill=text_color, font_size="64px", font_weight="bold",
                         font_family="Georgia, serif"))
        if subtitle:
            dwg.add(dwg.text(subtitle, insert=(width // 2, title_y + 80),
                             text_anchor="middle", dominant_baseline="middle",
                             fill=BRAND_TEXT, font_size="36px",
                             font_family="Arial, sans-serif"))
        dwg.save()
        return _svg_to_png(svg_path, png_path)
    except Exception as exc:
        log.error("create_banner failed: %s", exc)
        return f"Error: {exc}"
    finally:
        Path(svg_path).unlink(missing_ok=True)


def create_social_graphic(text: str, platform: str = "instagram", output_path: str = None) -> str:
    sizes = {"instagram": (1080, 1080), "facebook": (1200, 630),
              "twitter": (1200, 675), "linkedin": (1200, 627)}
    w, h = sizes.get(platform.lower(), (1080, 1080))
    return create_banner(text, width=w, height=h, output_path=output_path)


def create_quote_card(quote: str, attribution: str = "Dr. Bill Yomes",
                      output_path: str = None) -> str:
    import svgwrite
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    svg_path = str(EXPORTS_DIR / f"quote_{ts}.svg")
    png_path = output_path or str(EXPORTS_DIR / f"quote_{ts}.png")
    w, h = 1080, 1080
    try:
        dwg = svgwrite.Drawing(svg_path, size=(w, h))
        # Background
        dwg.add(dwg.rect(insert=(0, 0), size=(w, h), fill=BRAND_BG))
        # Gold border frame
        dwg.add(dwg.rect(insert=(40, 40), size=(w - 80, h - 80),
                         fill="none", stroke=BRAND_GOLD, stroke_width=3))
        # Decorative quote marks
        dwg.add(dwg.text("“", insert=(80, 200),
                         fill=BRAND_GOLD, font_size="180px",
                         font_family="Georgia, serif", opacity="0.4"))
        # Quote text — wrap at ~35 chars per line
        words = quote.split()
        lines, current = [], ""
        for word in words:
            if len(current) + len(word) + 1 > 35:
                lines.append(current.strip())
                current = word + " "
            else:
                current += word + " "
        if current.strip():
            lines.append(current.strip())
        total_lines = len(lines)
        start_y = h // 2 - (total_lines * 55) // 2
        for i, line in enumerate(lines):
            dwg.add(dwg.text(line, insert=(w // 2, start_y + i * 72),
                             text_anchor="middle", dominant_baseline="middle",
                             fill=BRAND_TEXT, font_size="48px",
                             font_family="Georgia, serif"))
        # Attribution
        dwg.add(dwg.text(f"— {attribution}", insert=(w // 2, h - 120),
                         text_anchor="middle", dominant_baseline="middle",
                         fill=BRAND_GOLD, font_size="32px",
                         font_family="Arial, sans-serif"))
        dwg.save()
        return _svg_to_png(svg_path, png_path)
    except Exception as exc:
        log.error("create_quote_card failed: %s", exc)
        return f"Error: {exc}"
    finally:
        Path(svg_path).unlink(missing_ok=True)


def run(message: str = None) -> str:
    if not message:
        return "SVG generator ready. Provide a quote or title."
    path = create_quote_card(message[:200])
    if path.startswith("Error"):
        return path
    import os
    import requests
    from core.vacation import vacation_gate
    bot_token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if bot_token and chat_id and not vacation_gate("normal", "jobs.design.svg_generator", message[:200]):
        try:
            with open(path, "rb") as f:
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendPhoto",
                    data={"chat_id": chat_id},
                    files={"photo": f},
                    timeout=30,
                )
        except Exception as exc:
            log.warning("Telegram send failed: %s", exc)
    return path
