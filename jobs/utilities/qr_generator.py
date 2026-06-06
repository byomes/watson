"""jobs/utilities/qr_generator.py — Generate QR code PNGs from text or URLs."""
import logging
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXPORT_DIR = REPO / "data" / "exports"

log = logging.getLogger(__name__)

_URL_RE = re.compile(r'https?://[^\s]+')


def generate_qr(text: str, output_path: str) -> bool:
    import qrcode
    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        img = qrcode.make(text)
        img.save(output_path)
        return True
    except Exception as exc:
        log.error("generate_qr failed: %s", exc)
        return False


def run(message: str = None) -> str:
    if not message:
        return "QR generator ready. Provide text or a URL to encode."
    url_match = _URL_RE.search(message)
    content = url_match.group(0) if url_match else message.strip()
    import time
    ts = int(time.time())
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = str(EXPORT_DIR / f"qr_{ts}.png")
    success = generate_qr(content, out_path)
    if success:
        return f"QR code generated: {out_path}\nEncoded: {content}"
    return "Failed to generate QR code."
