"""
watcher.py — runs on the PC, watches two audio folders.

Folder layout (configured via env or defaults below):
  INCOMING_DIR  →  weekly sermon  →  full pipeline
  ARCHIVE_DIR   →  old sermons    →  transcription + KB only

Usage:
  python jobs/watcher.py
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

load_dotenv()

log = logging.getLogger(__name__)

# --- Config -----------------------------------------------------------
INCOMING_DIR = Path(os.getenv("SERMON_INCOMING_DIR", r"E:\0 - Sermon Audio\incoming"))
ARCHIVE_DIR  = Path(os.getenv("SERMON_ARCHIVE_DIR",  r"E:\0 - Sermon Audio\archive"))
PROCESSED_DIR = INCOMING_DIR.parent / "processed"

AUDIO_EXTENSIONS = {".mp3", ".mp4", ".m4a", ".wav", ".flac", ".ogg", ".opus"}

# Seconds a file must be stable (unchanged) before we touch it
STABILITY_SECONDS = 10

# Path to this repo's root (parent of jobs/)
REPO_ROOT = Path(__file__).resolve().parent.parent


def _is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def _wait_for_stable(path: Path, stable_secs: int = STABILITY_SECONDS) -> bool:
    """Return True once the file size stops changing for stable_secs seconds."""
    prev_size = -1
    stable_count = 0
    for _ in range(60):          # max 60 × 5s = 5 minutes
        time.sleep(5)
        if not path.exists():
            return False
        size = path.stat().st_size
        if size == prev_size:
            stable_count += 1
            if stable_count >= (stable_secs // 5):
                return True
        else:
            stable_count = 0
            prev_size = size
    log.warning("File never stabilised: %s", path)
    return False


def _run_job(script: str, *args: str) -> bool:
    """Run a jobs/ script as a subprocess. Returns True on success."""
    cmd = [sys.executable, str(REPO_ROOT / "jobs" / script), *args]
    log.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        log.error("%s failed (exit %d)", script, result.returncode)
        return False
    return True


def handle_weekly(audio_path: Path):
    """Full pipeline: transcribe → cleanup → generate → notify."""
    log.info("Weekly pipeline starting: %s", audio_path.name)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # Stage 1 — transcribe
    if not _run_job("transcribe.py", str(audio_path), "--mode", "weekly"):
        log.error("Transcription failed — leaving file in incoming/")
        return

    # Stage 2 — cleanup (transcribe.py writes raw transcript path to stdout
    #            via a sentinel line; we re-derive the path here instead)
    from jobs.transcribe import raw_transcript_path   # local import to avoid circular
    raw_path = raw_transcript_path(audio_path)
    if not raw_path.exists():
        log.error("Expected raw transcript not found: %s", raw_path)
        return

    if not _run_job("cleanup.py", str(raw_path)):
        log.error("Cleanup failed")
        return

    clean_path = raw_path.parent / raw_path.name.replace("-raw.txt", "-clean.txt")
    if not clean_path.exists():
        log.error("Expected clean transcript not found: %s", clean_path)
        return

    # Stage 3 — generate blog draft + social seeds, push to Vercel KV, notify
    if not _run_job("generate.py", str(clean_path), audio_path.stem):
        log.error("Generate failed")
        return

    # Move audio to processed/
    dest = PROCESSED_DIR / audio_path.name
    audio_path.rename(dest)
    log.info("Audio moved to processed/: %s", dest)
    log.info("Weekly pipeline complete for: %s", audio_path.name)


def handle_archive(audio_path: Path):
    """Archive pipeline: transcribe only → kb/."""
    log.info("Archive pipeline starting: %s", audio_path.name)

    if not _run_job("transcribe.py", str(audio_path), "--mode", "archive"):
        log.error("Archive transcription failed — leaving file in archive/")
        return

    log.info("Archive pipeline complete for: %s", audio_path.name)


class AudioHandler(FileSystemEventHandler):
    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode   # "weekly" or "archive"
        self._seen: set[Path] = set()

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if not _is_audio(path):
            return
        if path in self._seen:
            return
        self._seen.add(path)
        log.info("Detected new audio (%s): %s", self.mode, path.name)

        if not _wait_for_stable(path):
            self._seen.discard(path)
            return

        if self.mode == "weekly":
            handle_weekly(path)
        else:
            handle_archive(path)

        self._seen.discard(path)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    observer = Observer()
    observer.schedule(AudioHandler("weekly"), str(INCOMING_DIR), recursive=False)
    observer.schedule(AudioHandler("archive"), str(ARCHIVE_DIR), recursive=False)
    observer.start()

    log.info("Watching for sermons:")
    log.info("  Weekly  → %s", INCOMING_DIR)
    log.info("  Archive → %s", ARCHIVE_DIR)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
