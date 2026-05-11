"""
cleanup.py — Claude API call: raw transcript → clean transcript.

Usage:
  python jobs/cleanup.py <raw_transcript_path>

Reads:  outputs/transcripts/raw/<stem>-raw.txt
Writes: outputs/transcripts/clean/<stem>-clean.txt
"""

import logging
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

REPO_ROOT  = Path(__file__).resolve().parent.parent
CLEAN_DIR  = REPO_ROOT / "outputs" / "transcripts" / "clean"
PROMPT_FILE = REPO_ROOT / "prompts" / "cleanup.md"

# Claude model — sonnet is fast and cheap enough for transcript cleanup
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 8192


def load_system_prompt() -> str:
    if PROMPT_FILE.exists():
        return PROMPT_FILE.read_text(encoding="utf-8")
    # Inline fallback if prompt file is missing
    return (
        "You are cleaning up a raw sermon transcript produced by Whisper speech-to-text. "
        "Your job:\n"
        "1. Fix obvious transcription errors (wrong words, missing punctuation).\n"
        "2. Remove filler words (um, uh, you know, like) and false starts.\n"
        "3. Break the text into clear paragraphs.\n"
        "4. Do NOT add content, commentary, or summaries — only clean what is there.\n"
        "5. Preserve the speaker's voice and vocabulary.\n"
        "Return only the cleaned transcript text."
    )


def cleanup(raw_path: Path) -> Path:
    raw_text = raw_path.read_text(encoding="utf-8")

    system_prompt = load_system_prompt()

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    log.info("Sending transcript to Claude for cleanup (%d chars)...", len(raw_text))
    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": raw_text}],
    )

    clean_text = message.content[0].text.strip()

    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    stem = raw_path.stem.replace("-raw", "")
    clean_path = CLEAN_DIR / f"{stem}-clean.txt"
    clean_path.write_text(clean_text, encoding="utf-8")

    log.info("Clean transcript written: %s (%d chars)", clean_path, len(clean_text))
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
