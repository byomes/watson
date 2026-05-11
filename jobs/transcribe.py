"""
transcribe.py — Whisper transcription for both pipeline modes.

Usage:
  python jobs/transcribe.py <audio_path> --mode weekly
  python jobs/transcribe.py <audio_path> --mode archive
  python jobs/transcribe.py <audio_path> --mode archive --model base

Weekly mode:  writes to outputs/transcripts/raw/<stem>-raw.txt
Archive mode: writes to kb/transcripts/<stem>.txt

Model defaults (override with --model):
  weekly  -> large
  archive -> small
"""

import argparse
import ctypes
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Default models per mode -- override via --model or env vars
DEFAULT_MODEL_WEEKLY  = os.getenv("WHISPER_MODEL_WEEKLY",  "large")
DEFAULT_MODEL_ARCHIVE = os.getenv("WHISPER_MODEL_ARCHIVE", "small")

RAW_DIR = REPO_ROOT / "outputs" / "transcripts" / "raw"
KB_DIR  = REPO_ROOT / "kb" / "transcripts"


def _short_path(path: Path) -> str:
    """
    Convert a long Windows path to its 8.3 short-path form so ffmpeg
    can open files in folders whose names contain spaces.
    Returns the original path string on non-Windows or if conversion fails.
    """
    if sys.platform != "win32":
        return str(path)
    try:
        buf = ctypes.create_unicode_buffer(32768)
        get_short = ctypes.windll.kernel32.GetShortPathNameW
        get_short(str(path), buf, len(buf))
        return buf.value or str(path)
    except Exception:
        return str(path)


def raw_transcript_path(audio_path: Path) -> Path:
    """Return the expected raw transcript path for a given audio file."""
    return RAW_DIR / f"{audio_path.stem}-raw.txt"


def transcribe(audio_path: Path, mode: str, model_name: str) -> Path:
    """
    Run Whisper on audio_path.
    Returns the path to the written transcript file.
    """
    import whisper

    log.info("Loading Whisper model: %s", model_name)
    model = whisper.load_model(model_name)

    safe_path = _short_path(audio_path)
    log.info("Transcribing: %s", audio_path.name)
    result = model.transcribe(safe_path, verbose=True)
    text = result["text"].strip()

    if mode == "archive":
        KB_DIR.mkdir(parents=True, exist_ok=True)
        out_path = KB_DIR / f"{audio_path.stem}.txt"
    else:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        out_path = raw_transcript_path(audio_path)

    out_path.write_text(text, encoding="utf-8")
    log.info("Transcript written: %s (%d chars)", out_path, len(text))
    return out_path


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Whisper transcription job")
    parser.add_argument("audio_path", help="Path to the audio file")
    parser.add_argument(
        "--mode",
        choices=["weekly", "archive"],
        default="weekly",
        help="weekly = full pipeline; archive = KB storage only",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Whisper model to use (tiny, base, small, medium, large). "
             "Defaults to 'large' for weekly, 'small' for archive.",
    )
    args = parser.parse_args()

    audio_path = Path(args.audio_path)
    if not audio_path.exists():
        log.error("Audio file not found: %s", audio_path)
        sys.exit(1)

    # Resolve model: explicit flag > mode default
    if args.model:
        model_name = args.model
    elif args.mode == "archive":
        model_name = DEFAULT_MODEL_ARCHIVE
    else:
        model_name = DEFAULT_MODEL_WEEKLY

    log.info("Mode: %s | Model: %s", args.mode, model_name)

    out_path = transcribe(audio_path, args.mode, model_name)
    print(f"TRANSCRIPT:{out_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
