"""Breath detection + suppression for the ACX mastering pipeline.

Prior sessions' pipelines never suppressed breaths, and quiet intake breaths
were a real contributor to noise-floor failures. This module finds them and
hands master_chapter.py a time-gated ffmpeg attenuation filter.

A breath is defined operationally as a window that is DISTINCT FROM BOTH
silence AND speech:
  - it has real energy, unlike room tone (RMS above the silence floor);
  - it is quieter than narration (RMS below the speech ceiling);
  - its energy is low/mid-band, unlike voiced/sibilant speech (spectral
    centroid below a cap);
  - it is short (a fraction of a second), unlike a sustained pause; and
  - it sits near speech, unlike a stretch of steady background hum.

Detection is pure measurement on decoded PCM (numpy) — no audio is altered
here. Suppression is done by ffmpeg via a `volume` filter gated to the
detected time ranges, so ffmpeg remains the only thing that touches samples,
matching the house pattern.
"""

import numpy as np

import config
import ffmpeg_utils


def _frame_features(samples, sr, frame_len):
    """Return per-frame (rms_db, centroid_hz) arrays over non-overlapping
    frames of length frame_len."""
    n_frames = len(samples) // frame_len
    if n_frames == 0:
        return np.array([]), np.array([])
    frames = samples[: n_frames * frame_len].reshape(n_frames, frame_len)

    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    rms_db = 20.0 * np.log10(np.maximum(rms, 1e-9))

    window = np.hanning(frame_len)
    mags = np.abs(np.fft.rfft(frames * window, axis=1))
    freqs = np.fft.rfftfreq(frame_len, d=1.0 / sr)
    mag_sum = mags.sum(axis=1)
    centroid = np.where(
        mag_sum > 0, (mags * freqs).sum(axis=1) / np.maximum(mag_sum, 1e-12), 0.0
    )
    return rms_db, centroid


def _contiguous_runs(mask):
    """Yield (start_idx, end_idx_exclusive) for each run of True in mask."""
    if not mask.any():
        return
    idx = np.flatnonzero(mask)
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([idx[0]], idx[breaks + 1]))
    ends = np.concatenate((idx[breaks], [idx[-1]]))
    for s, e in zip(starts, ends):
        yield int(s), int(e) + 1


def detect(path, prior=None, log_lines=None):
    """Detect breath intervals in an audio file.

    Returns (intervals, stats) where intervals is a list of (start_s, end_s)
    and stats summarizes breath acoustics + pacing for the voice profile.
    """
    prior = prior or {}
    rms_floor = prior.get("rms_floor_db", config.BREATH_RMS_FLOOR_DB)
    rms_ceil = prior.get("rms_ceil_db", config.BREATH_RMS_CEIL_DB)
    centroid_max = prior.get("centroid_max_hz", config.BREATH_CENTROID_MAX_HZ)

    samples, sr = ffmpeg_utils.decode_pcm(path)
    frame_len = max(1, int(sr * config.BREATH_FRAME_MS / 1000.0))
    frame_s = frame_len / sr
    rms_db, centroid = _frame_features(samples, sr, frame_len)

    empty_stats = {"count": 0, "count_per_min": 0.0, "amp_db_mean": None,
                   "centroid_hz_mean": None, "pause_s_mean": None,
                   "speech_ratio": None}
    if rms_db.size == 0:
        return [], empty_stats

    is_silence = rms_db < rms_floor
    is_speech = rms_db > rms_ceil
    # Breath candidate: in the mid amplitude band AND low/mid spectral band.
    candidate = (~is_silence) & (~is_speech) & (centroid <= centroid_max)

    min_frames = max(1, int(round(config.BREATH_MIN_DURATION_S / frame_s)))
    max_frames = max(min_frames, int(round(config.BREATH_MAX_DURATION_S / frame_s)))
    neighbor_frames = max(1, int(round(0.5 / frame_s)))  # speech must be within 0.5s

    intervals = []
    breath_frame_idx = []
    for s, e in _contiguous_runs(candidate):
        length = e - s
        if length < min_frames or length > max_frames:
            continue
        # A breath sits near speech; steady low-level hum away from any speech
        # is not a breath.
        lo = max(0, s - neighbor_frames)
        hi = min(len(is_speech), e + neighbor_frames)
        if not is_speech[lo:hi].any():
            continue
        intervals.append((round(s * frame_s, 4), round(e * frame_s, 4)))
        breath_frame_idx.extend(range(s, e))

    # --- stats for the voice profile ---------------------------------------
    total_s = len(rms_db) * frame_s
    stats = dict(empty_stats)
    stats["count"] = len(intervals)
    stats["count_per_min"] = round(len(intervals) / (total_s / 60.0), 2) if total_s else 0.0
    stats["speech_ratio"] = round(float(is_speech.mean()), 3)
    if breath_frame_idx:
        bidx = np.array(breath_frame_idx)
        stats["amp_db_mean"] = round(float(rms_db[bidx].mean()), 2)
        stats["centroid_hz_mean"] = round(float(centroid[bidx].mean()), 1)
    # Pause statistics: mean length of silence runs (room-tone gaps).
    pause_lengths = [(e - s) * frame_s for s, e in _contiguous_runs(is_silence)]
    if pause_lengths:
        stats["pause_s_mean"] = round(float(np.mean(pause_lengths)), 3)

    if log_lines is not None:
        log_lines.append(
            f"Breath detection: {len(intervals)} breath(s) "
            f"({stats['count_per_min']}/min), "
            f"mean amp {stats['amp_db_mean']} dB, "
            f"mean centroid {stats['centroid_hz_mean']} Hz "
            f"[band {rms_floor:.0f}..{rms_ceil:.0f} dB, centroid<= {centroid_max:.0f} Hz]"
        )
    return intervals, stats


def suppression_filter(intervals, attenuation_db=None):
    """Build an ffmpeg `volume` filter string that ducks exactly the detected
    breath intervals by attenuation_db, or None if there is nothing to duck.

    Suppress rather than hard-cut: an intake breath ducked ~30 dB drops well
    below the -60 dB noise-floor spec while leaving a natural low-level trace,
    which reads more human than a dead hole. Ducking an already-quiet region
    by a fixed gain does not click in practice.
    """
    if not intervals:
        return None
    attenuation_db = config.BREATH_ATTENUATION_DB if attenuation_db is None else attenuation_db
    gain_lin = 10.0 ** (attenuation_db / 20.0)
    terms = "+".join(f"between(t,{a},{b})" for a, b in intervals)
    return f"volume=volume={gain_lin:.6f}:enable='{terms}'"
