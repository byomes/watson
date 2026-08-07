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

# --- High-pass filter (step before denoise) ---------------------------------
# Removes sub-vocal rumble / HVAC / plosive DC energy that inflates the noise
# floor without touching the male voice fundamental (~85-180Hz). 80Hz is the
# ACX-safe default; the retry loop can raise it if the noise floor still fails.
HIGHPASS_FREQ = 80
HIGHPASS_FREQ_MAX = 120  # retry ceiling — above this we risk thinning the voice

# --- afftdn (noise reduction) — MODERATE DEFAULTS, RETRY LOOP TUNES UPWARD ---
# nr = noise reduction amount in dB, nf = noise floor in dB. These are
# ffmpeg's own moderate defaults; the retry loop raises AFFTDN_NR toward
# AFFTDN_NR_MAX whenever the noise-floor spec fails, and by how much scales
# with how far over -60dB the measured floor landed.
AFFTDN_NR = 12
AFFTDN_NF = -50
AFFTDN_NR_MAX = 40  # afftdn's own hard ceiling for nr

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

# --- Breath detection / suppression -----------------------------------------
# A breath is a low-amplitude, low-frequency, short-duration window that sits
# BETWEEN silence and speech: it has real energy (unlike room tone) but is
# quieter than narration and carries little high-frequency (consonant) content.
# Detection runs in Python on decoded PCM (numpy) — measurement, not
# processing; ffmpeg still does the actual attenuation via a time-gated
# volume filter. All four gates below must hold for a frame to be a breath
# candidate; a persistent voice profile narrows them per narrator.
BREATH_FRAME_MS = 20            # analysis hop
BREATH_MIN_DURATION_S = 0.08    # shorter runs are consonant transients, not breaths
BREATH_MAX_DURATION_S = 0.70    # longer low-level runs are pauses/room tone
BREATH_RMS_FLOOR_DB = -55.0     # below this is silence, not a breath
BREATH_RMS_CEIL_DB = -28.0      # above this is speech, not a breath
BREATH_CENTROID_MAX_HZ = 1800.0 # breaths are low/mid-band; consonants sit higher
BREATH_ATTENUATION_DB = -30.0   # how hard to duck a detected breath (suppress, not hard-cut)
BREATH_ATTENUATION_MAX_DB = -50.0  # retry loop can duck harder if noise floor still fails
BREATH_FADE_MS = 8              # ramp in/out of each duck to avoid clicks

# --- Voice profile (persistent, per-narrator, learned prior) ----------------
# Lives under the repo-wide data/ dir alongside the other JSON stores, not in
# the job folder — it is narrator state, not book state, and survives across
# books. Updated ONLY after a chapter passes QC.
DEFAULT_NARRATOR = "bill"
VOICE_PROFILE_PATH = BASE_DIR.parent.parent / "data" / "audiobook_voice_profiles.json"

# --- Retry loop --------------------------------------------------------------
MAX_RETRY_ATTEMPTS = 5  # master+score attempts before flagging for manual review

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
# ACX Check plugin measures the noise floor as the RMS of the QUIETEST window
# of this length, NOT ffmpeg astats' whole-file noise-floor estimate. We match
# the plugin so our PASS/FAIL agrees with a manual Audacity spot-check.
ACX_NOISE_FLOOR_WINDOW_S = 0.5
SPEC_SAMPLE_RATE = 44100
SPEC_BITRATE = 192000  # bps
SPEC_CHANNELS = 1
SPEC_HEAD_SILENCE_MIN = 0.5
SPEC_HEAD_SILENCE_MAX = 1.0
SPEC_TAIL_SILENCE_MIN = 1.0
SPEC_TAIL_SILENCE_MAX = 5.0

# Book-level RMS deviation flag threshold
BOOK_RMS_DEVIATION_DB = 1.0
