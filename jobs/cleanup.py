"""
cleanup.py — Passes raw Whisper transcript to clean dir unchanged.

No API call. Whisper output is clean enough; review happens in claude.ai
at the generate/draft stage.

Usage:
  python jobs/cleanup.py <raw_transcript_path>

Reads:  outputs/transcripts/raw/<stem>-raw.txt
Writes: outputs/transcripts/clean/<stem>-clean.txt
"""

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CLEAN_DIR = REPO_ROOT / "outputs" / "transcripts" / "clean"


def cleanup(raw_path: Path) -> Path:
    raw_text = raw_path.read_text(encoding="utf-8")

    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    stem = raw_path.stem.replace("-raw", "")
    clean_path = CLEAN_DIR / f"{stem}-clean.txt"
    clean_path.write_text(raw_text, encoding="utf-8")

    log.info("Transcript passed through to clean: %s (%d chars)", clean_path, len(raw_text))
    return clean_path


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) < 2:
        print("Usage: python jobs/cleanup.py <raw_transcript_path>")
        sys.exit(1)

    raw_path = Path(sys.argv[1])
    if not raw_path.exists():
        log.error("Raw transcript not found: %s", raw_path)
        sys.exit(1)

    clean_path = cleanup(raw_path)
    print(f"CLEAN:{clean_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
