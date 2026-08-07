"""Constants for the ACX audiobook mastering pipeline. All checks are
deterministic ffmpeg/ffprobe measurements against these thresholds — no
AI-generated judgment calls, matching the house pattern for QC jobs.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "input"
PROCESSED_DIR = BASE_DIR / "processed"
SAMPLES_DIR = BASE_DIR / "samples"
REPORTS_DIR = BASE_DIR / "reports"
LOGS_DIR = BASE_DIR / "logs"

QC_DATA_PATH = REPORTS_DIR / "qc_data.json"
QC_REPORT_MD = REPORTS_DIR / "qc_report.md"
QC_REPORT_CSV = REPORTS_DIR / "qc_report.csv"

FILENAME_PREFIX = "Yomes_WJ"

# --- Loudness / ACX targets -------------------------------------------------
TARGET_INTEGRATED_LOUDNESS = -20.5  # LUFS, center of ACX's -23..-18 RMS band
TARGET_TRUE_PEAK = -3.0             # dBTP ceiling
TARGET_LRA = 11.0

# --- Padding (real digital silence added at step 8) -------------------------
HEAD_PAD_SECONDS = 0.5   # within spec's 0.5-1s head range
TAIL_PAD_SECONDS = 1.0   # within spec's 1-5s tail range

# --- Silence detection (used to find/trim existing room tone) ---------------
SILENCE_NOISE_DB = -50   # dB threshold below which audio is "silence"
SILENCE_MIN_DURATION = 0.3  # seconds

# --- afftdn (noise reduction) — MODERATE DEFAULTS, NEEDS REAL-CHAPTER TUNING
# nr = noise reduction amount in dB, nf = noise floor in dB. These are
# ffmpeg's own moderate defaults, not hand-tuned against real narration yet.
AFFTDN_NR = 12
AFFTDN_NF = -50

# --- De-click / breath-taming — MODERATE, must not strip breaths ------------
# adeclick: default-ish settings, tuned down slightly so it only catches
# real impulsive clicks, not natural consonant transients.
ADECLICK_THRESHOLD = 2
ADECLICK_BURST = 2
# agate: gentle ratio (1.5) so quiet passages (breaths) are tamed, not gated
# to silence. threshold is well below narration level, above noise floor.
AGATE_THRESHOLD_DB = -45
AGATE_RATIO = 1.5
AGATE_ATTACK_MS = 5
AGATE_RELEASE_MS = 100

# --- Export ------------------------------------------------------------------
EXPORT_SAMPLE_RATE = 44100
EXPORT_CHANNELS = 1
EXPORT_BITRATE = "192k"

# --- Retail sample -----------------------------------------------------------
SAMPLE_DEFAULT_START_SECONDS = 0
SAMPLE_DEFAULT_DURATION_SECONDS = 300  # first 5 minutes

# --- QC spec thresholds (8 individual PASS/FAIL checks, ACX submission specs)
SPEC_RMS_MIN = -23.0
SPEC_RMS_MAX = -18.0
SPEC_TRUE_PEAK_MAX = -3.0
SPEC_NOISE_FLOOR_MAX = -60.0
SPEC_SAMPLE_RATE = 44100
SPEC_BITRATE = 192000  # bps
SPEC_CHANNELS = 1
SPEC_HEAD_SILENCE_MIN = 0.5
SPEC_HEAD_SILENCE_MAX = 1.0
SPEC_TAIL_SILENCE_MIN = 1.0
SPEC_TAIL_SILENCE_MAX = 5.0

# Book-level RMS deviation flag threshold
BOOK_RMS_DEVIATION_DB = 1.0
