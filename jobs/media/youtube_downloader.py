"""jobs/media/youtube_downloader.py — Download audio from YouTube videos using yt-dlp."""
import logging
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MEDIA_DIR = REPO / "data" / "media"

log = logging.getLogger(__name__)

_URL_RE = re.compile(r'https?://(?:www\.)?(?:youtube\.com/watch\?[^\s]*v=|youtu\.be/)[^\s]+')


def download_audio(url: str, output_path: str = None) -> str:
    import yt_dlp
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    out_tmpl = output_path or str(MEDIA_DIR / "%(title)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_tmpl,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "audio")
            filename = ydl.prepare_filename(info).rsplit(".", 1)[0] + ".mp3"
            return filename
    except Exception as exc:
        log.error("yt-dlp failed: %s", exc)
        return f"Download failed: {exc}"


def run(message: str = None) -> str:
    if not message:
        return "YouTube downloader ready. Provide a YouTube URL."
    match = _URL_RE.search(message)
    if not match:
        return "No YouTube URL found in message."
    url = match.group(0)
    result = download_audio(url)
    if result.startswith("Download failed"):
        return result
    return f"Audio downloaded: {result}"
