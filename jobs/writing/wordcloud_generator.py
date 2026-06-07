"""jobs/writing/wordcloud_generator.py — generate word cloud images from text."""
import logging
import os
import re
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO / "outputs" / "wordclouds"


def _send_telegram_photo(photo_path: str, caption: str = "") -> None:
    bot_token = os.getenv("WATSON_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("WATSON_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        return
    try:
        import requests
        with open(photo_path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption[:200]},
                files={"photo": f},
                timeout=30,
            )
    except Exception as exc:
        log.warning("Telegram photo send failed: %s", exc)


def generate_wordcloud(text: str, title: str = "", output_path: str = None, send_telegram: bool = True) -> dict:
    try:
        from wordcloud import WordCloud, STOPWORDS
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        return {"success": False, "path": "", "error": f"Missing library: {exc}"}

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not output_path:
        slug = re.sub(r'[^\w]+', '-', title.lower()).strip('-') or "wordcloud"
        date = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        output_path = str(OUTPUT_DIR / f"{date}-{slug}.png")

    stopwords = set(STOPWORDS) | {
        "said", "will", "also", "one", "two", "three", "like", "just",
        "know", "think", "well", "got", "get", "would", "could", "should",
    }

    wc = WordCloud(
        width=1200,
        height=600,
        background_color="white",
        stopwords=stopwords,
        max_words=200,
        collocations=True,
        prefer_horizontal=0.7,
    ).generate(text)

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.imshow(wc, interpolation="bilinear")
    ax.axis("off")
    if title:
        ax.set_title(title, fontsize=16, pad=10)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    if send_telegram:
        _send_telegram_photo(output_path, caption=f"Word cloud: {title or 'result'}")

    word_count = len(re.findall(r'\b\w+\b', text))
    return {"success": True, "path": output_path, "word_count": word_count, "error": None}


def generate_from_file(file_path: str, send_telegram: bool = True) -> dict:
    p = Path(file_path).expanduser()
    if not p.exists():
        return {"success": False, "path": "", "error": f"File not found: {file_path}"}
    text = p.read_text(encoding="utf-8", errors="ignore")
    # Strip markdown
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*{1,2}(.+?)\*{1,2}', r'\1', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    return generate_wordcloud(text, title=p.stem, send_telegram=send_telegram)


def run(message: str = None) -> str:
    if not message:
        return "Usage: word cloud <text or file path>"

    msg = message.strip()
    no_send = "no telegram" in msg.lower() or "no send" in msg.lower()
    msg_clean = re.sub(r'\s+(no telegram|no send)', '', msg, flags=re.IGNORECASE).strip()

    # File or inline text?
    if re.search(r'\.(txt|md|rst|pdf)$', msg_clean, re.IGNORECASE):
        result = generate_from_file(msg_clean, send_telegram=not no_send)
    else:
        result = generate_wordcloud(msg_clean, send_telegram=not no_send)

    if not result["success"]:
        return f"Word cloud failed: {result['error']}"
    return f"Word cloud generated ({result['word_count']} words): {result['path']}"
