"""
batch.py — Batch transcription of audio backlog into personal knowledge base.

Processes all audio files in a folder sequentially, one at a time.
Skips files already transcribed. Resume-safe — restart anytime and it
picks up where it left off.

Usage:
  py -3.11 jobs/batch.py                        # process SERMON_ARCHIVE_DIR
  py -3.11 jobs/batch.py --dir "E:\My Audio"    # process a custom folder
  py -3.11 jobs/batch.py --model large          # override model
  py -3.11 jobs/batch.py --dry-run              # list files without processing

Log is written to: outputs/logs/batch.log
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

REPO_ROOT    = Path(__file__).resolve().parent.parent
LOG_DIR      = REPO_ROOT / "outputs" / "logs"
KB_DIR       = REPO_ROOT / "kb" / "transcripts"

ARCHIVE_DIR  = Path(os.getenv("SERMON_ARCHIVE_DIR", r"E:\0 - Sermon Audio\archive"))
PROCESSED_DIR = ARCHIVE_DIR.parent / "processed"

AUDIO_EXTENSIONS = {".mp3", ".mp4", ".m4a", ".wav", ".flac", ".ogg", ".opus"}


def _already_transcribed(audio_path: Path) -> bool:
    """Check if a transcript already exists in kb/transcripts/ for this file."""
    expected = KB_DIR / f"{audio_path.stem}.txt"
    return expected.exists()


def _run_transcribe(audio_path: Path, model: str) -> bool:
    """Run transcribe.py on a single file. Returns True on success."""
    python_bin = os.getenv("PYTHON_BIN", sys.executable)
    cmd = python_bin.split() + [
        str(REPO_ROOT / "jobs" / "transcribe.py"),
        str(audio_path),
        "--mode", "archive",
        "--model", model,
    ]
    log.info("Transcribing: %s", audio_path.name)
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        log.error("Failed: %s (exit %d)", audio_path.name, result.returncode)
        return False
    return True


def _collect_audio(folder: Path) -> list[Path]:
    """Return sorted list of audio files in folder."""
    files = [
        f for f in sorted(folder.iterdir())
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    ]
    return files


def batch(folder: Path, model: str, dry_run: bool) -> None:
    files = _collect_audio(folder)

    if not files:
        log.info("No audio files found in %s", folder)
        return

    already_done  = [f for f in files if _already_transcribed(f)]
    to_process    = [f for f in files if not _already_transcribed(f)]

    log.info("Found %d audio files — %d already transcribed, %d to process",
             len(files), len(already_done), len(to_process))

    if dry_run:
        log.info("--- DRY RUN — no files will be processed ---")
        for f in to_process:
            log.info("  PENDING: %s", f.name)
        for f in already_done:
            log.info("  DONE:    %s", f.name)
        return

    if not to_process:
        log.info("All files already transcribed. Nothing to do.")
        return

    success_count = 0
    fail_count    = 0
    start_time    = time.time()

    for i, audio_path in enumerate(to_process, 1):
        log.info("--- [%d/%d] %s ---", i, len(to_process), audio_path.name)
        file_start = time.time()

        ok = _run_transcribe(audio_path, model)

        elapsed = time.time() - file_start
        total_elapsed = time.time() - start_time
        remaining = len(to_process) - i
        avg_per_file = total_elapsed / i
        eta_seconds = avg_per_file * remaining

        if ok:
            success_count += 1
            log.info("Done in %.0fs — %d remaining — ETA ~%.0f min",
                     elapsed, remaining, eta_seconds / 60)
        else:
            fail_count += 1
            log.warning("Skipping failed file and continuing")

    log.info("=== Batch complete: %d succeeded, %d failed, total time %.0f min ===",
             success_count, fail_count, (time.time() - start_time) / 60)


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "batch.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ]
    )

    parser = argparse.ArgumentParser(description="Batch transcription job")
    parser.add_argument(
        "--dir",
        default=str(ARCHIVE_DIR),
        help=f"Folder to process (default: SERMON_ARCHIVE_DIR = {ARCHIVE_DIR})",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("WHISPER_MODEL_ARCHIVE", "medium"),
        help="Whisper model to use (default: WHISPER_MODEL_ARCHIVE or medium)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files without processing",
    )
    args = parser.parse_args()

    folder = Path(args.dir)
    if not folder.exists():
        log.error("Folder not found: %s", folder)
        sys.exit(1)

    log.info("Batch transcription starting")
    log.info("  Folder: %s", folder)
    log.info("  Model:  %s", args.model)
    log.info("  Log:    %s", log_file)

    batch(folder, args.model, args.dry_run)


if __name__ == "__main__":
    main()