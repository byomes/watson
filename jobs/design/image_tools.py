"""jobs/design/image_tools.py — Resize, watermark, optimize, and convert images."""
import logging
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXPORTS_DIR = REPO / "data" / "exports"

log = logging.getLogger(__name__)
_PATH_RE = re.compile(r'[\w/~.\-]+\.(?:jpg|jpeg|png|webp|gif|heic)', re.IGNORECASE)

SOCIAL_SIZES = {
    "instagram": (1080, 1080),
    "facebook": (1200, 630),
    "twitter": (1200, 675),
    "linkedin": (1200, 627),
}


def _out(path: str, suffix: str, fmt: str = None) -> str:
    p = Path(path)
    ext = f".{fmt}" if fmt else p.suffix
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return str(EXPORTS_DIR / f"{p.stem}_{suffix}{ext}")


def resize_image(path: str, width: int, height: int, output_path: str = None) -> str:
    from PIL import Image
    out = output_path or _out(path, f"{width}x{height}")
    try:
        with Image.open(path) as img:
            img = img.resize((width, height), Image.LANCZOS)
            img.save(out)
        return out
    except Exception as exc:
        log.error("resize_image failed: %s", exc)
        return f"Error: {exc}"


def watermark(path: str, text: str, output_path: str = None) -> str:
    from PIL import Image, ImageDraw, ImageFont
    out = output_path or _out(path, "watermarked")
    try:
        with Image.open(path).convert("RGBA") as img:
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            font_size = max(20, img.width // 30)
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()
            margin = 20
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x, y = img.width - tw - margin, img.height - th - margin
            draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 100))
            draw.text((x, y), text, font=font, fill=(201, 168, 76, 180))
            combined = Image.alpha_composite(img, overlay)
            combined.convert("RGB").save(out)
        return out
    except Exception as exc:
        log.error("watermark failed: %s", exc)
        return f"Error: {exc}"


def remove_background(path: str, output_path: str = None) -> str:
    from rembg import remove
    from PIL import Image
    out = output_path or _out(path, "nobg", "png")
    try:
        with open(path, "rb") as f:
            result = remove(f.read())
        with open(out, "wb") as f:
            f.write(result)
        return out
    except Exception as exc:
        log.error("remove_background failed: %s", exc)
        return f"Error: {exc}"


def convert_format(path: str, format: str, output_path: str = None) -> str:
    from PIL import Image
    fmt = format.lower().lstrip(".")
    out = output_path or _out(path, "converted", fmt)
    try:
        with Image.open(path) as img:
            save_fmt = "JPEG" if fmt in ("jpg", "jpeg") else fmt.upper()
            if save_fmt == "JPEG" and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(out, format=save_fmt)
        return out
    except Exception as exc:
        log.error("convert_format failed: %s", exc)
        return f"Error: {exc}"


def optimize_for_web(path: str, output_path: str = None) -> str:
    from PIL import Image
    out = output_path or _out(path, "web", "webp")
    try:
        with Image.open(path) as img:
            if img.width > 1200:
                ratio = 1200 / img.width
                img = img.resize((1200, int(img.height * ratio)), Image.LANCZOS)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(out, format="WEBP", quality=82, optimize=True)
        return out
    except Exception as exc:
        log.error("optimize_for_web failed: %s", exc)
        return f"Error: {exc}"


def optimize_for_social(path: str, platform: str = "instagram", output_path: str = None) -> str:
    from PIL import Image, ImageOps
    dims = SOCIAL_SIZES.get(platform.lower(), (1080, 1080))
    out = output_path or _out(path, platform)
    try:
        with Image.open(path) as img:
            img = ImageOps.fit(img, dims, Image.LANCZOS)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(out)
        return out
    except Exception as exc:
        log.error("optimize_for_social failed: %s", exc)
        return f"Error: {exc}"


def run(message: str = None) -> str:
    if not message:
        return "Image tools ready. Provide an image path and operation."
    match = _PATH_RE.search(message)
    if not match:
        return "Image tools ready. No image file path found in message."
    path = match.group(0).replace("~", os.path.expanduser("~"))
    if "web" in message.lower():
        return optimize_for_web(path)
    if any(p in message.lower() for p in ("instagram", "facebook", "twitter", "linkedin")):
        platform = next(p for p in ("instagram", "facebook", "twitter", "linkedin") if p in message.lower())
        return optimize_for_social(path, platform)
    return f"Image found: {path}. Specify operation: optimize, watermark, resize, convert, remove background."
