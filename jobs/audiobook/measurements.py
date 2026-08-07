"""Shared QC measurement + spec evaluation, used by both master_chapter.py
(right after export) and qc_report.py (--regenerate). One measurement path
so a file is never scored two different ways depending on which script
touched it last.
"""

import numpy as np

import config, ffmpeg_utils


def acx_noise_floor_db(path, skip_head_s=None, skip_tail_s=None):
    """RMS of the QUIETEST fixed-length window in the file, in dB — the metric
    the Audacity ACX Check plugin reports as 'noise floor', not ffmpeg astats'
    whole-file estimate. Sliding the window via a cumulative sum of squares so
    a long chapter still measures in one pass.

    The pipeline pads every export with true digital silence (head + tail). If
    that padding were included, the quietest window would ALWAYS be the dead
    padding (-inf) and the noise-floor check would be meaningless — a chapter
    riddled with loud breaths would still 'pass'. So the padding regions are
    excluded and the floor is measured over the narration body only, which is
    where room tone and any residual breath actually live. Defaults skip
    exactly the pad lengths this pipeline adds (plus a small guard); callers
    measuring a non-padded file can pass 0.

    Returns None if the body is shorter than one window."""
    skip_head_s = (config.HEAD_PAD_SECONDS + 0.1) if skip_head_s is None else skip_head_s
    skip_tail_s = (config.TAIL_PAD_SECONDS + 0.1) if skip_tail_s is None else skip_tail_s
    samples, sr = ffmpeg_utils.decode_pcm(path)
    win = int(sr * config.ACX_NOISE_FLOOR_WINDOW_S)
    if win <= 0:
        return None

    start = int(skip_head_s * sr)
    stop = len(samples) - int(skip_tail_s * sr)
    body = samples[start:stop]
    if len(body) < win:
        # Body too short to measure after excluding padding — fall back to the
        # whole file rather than returning nothing.
        body = samples
    if len(body) < win:
        return None

    sq = body.astype(np.float64) ** 2
    csum = np.concatenate(([0.0], np.cumsum(sq)))
    # window_sum_of_squares[i] = sum(sq[i : i+win])
    window_ss = csum[win:] - csum[:-win]
    min_ms = float(window_ss.min()) / win
    if min_ms <= 0:
        return -float("inf")
    # Native float, not np.float64 — a numpy scalar propagates through the
    # spec comparisons into a np.bool_ that json.dump can't serialize.
    return round(float(20.0 * np.log10(np.sqrt(min_ms))), 2)


def measure_file(mp3_path, log_lines=None):
    """Runs every QC measurement against a single exported MP3. Returns a
    flat dict — this is the only place ffmpeg gets invoked for QC purposes."""
    info = ffmpeg_utils.probe(mp3_path)
    astats = ffmpeg_utils.measure_astats(mp3_path, log_lines=log_lines)
    # loudnorm's own measurement pass gives ITU-R BS.1770 integrated
    # loudness and true peak — this is what replaces a naive sample-peak
    # check per the spec.
    loud = ffmpeg_utils.loudnorm_measure(mp3_path, log_lines=log_lines)
    silences = ffmpeg_utils.detect_silence(mp3_path, log_lines=log_lines)

    head_silence = 0.0
    tail_silence = 0.0
    if silences:
        first_start, first_end = silences[0]
        if first_start <= 0.05:
            head_silence = first_end - first_start
        last_start, last_end = silences[-1]
        if info["duration"] and (info["duration"] - last_end) <= 0.05:
            tail_silence = last_end - last_start

    # ACX-method noise floor (quietest 0.5s window RMS) is what the spec is
    # judged against; astats' whole-file estimate is kept for diagnostics only.
    acx_noise_floor = acx_noise_floor_db(mp3_path)

    return {
        "filename": mp3_path.name,
        "duration": info["duration"],
        "sample_rate": info["sample_rate"],
        "channels": info["channels"],
        "bit_rate": info["bit_rate"],
        "codec_name": info["codec_name"],
        "integrated_loudness_db": float(loud["input_i"]),
        "true_peak_db": float(loud["input_tp"]),
        "noise_floor_db": acx_noise_floor,
        "astats_noise_floor_db": astats["noise_floor_db"],
        "head_silence_s": round(head_silence, 3),
        "tail_silence_s": round(tail_silence, 3),
    }


def evaluate_specs(m):
    """Returns (specs, overall_pass). specs is an ordered list of
    (name, value_str, requirement_str, passed) — the 8 individual ACX
    submission checks. Deterministic threshold comparisons only."""

    def in_range(value, lo, hi):
        return value is not None and lo <= value <= hi

    specs = [
        (
            "Integrated loudness (RMS)",
            f"{m['integrated_loudness_db']:.2f} dB",
            f"{config.SPEC_RMS_MIN} to {config.SPEC_RMS_MAX} dB",
            in_range(m["integrated_loudness_db"], config.SPEC_RMS_MIN, config.SPEC_RMS_MAX),
        ),
        (
            "True peak",
            f"{m['true_peak_db']:.2f} dBTP",
            f"<= {config.SPEC_TRUE_PEAK_MAX} dBTP",
            m["true_peak_db"] is not None and m["true_peak_db"] <= config.SPEC_TRUE_PEAK_MAX,
        ),
        (
            "Noise floor",
            f"{m['noise_floor_db']:.2f} dB" if m["noise_floor_db"] is not None else "n/a",
            f"<= {config.SPEC_NOISE_FLOOR_MAX} dB",
            m["noise_floor_db"] is not None and m["noise_floor_db"] <= config.SPEC_NOISE_FLOOR_MAX,
        ),
        (
            "Sample rate",
            f"{m['sample_rate']} Hz",
            f"{config.SPEC_SAMPLE_RATE} Hz",
            m["sample_rate"] == config.SPEC_SAMPLE_RATE,
        ),
        (
            "Bitrate (CBR)",
            f"{round(m['bit_rate'] / 1000)} kbps",
            f"{config.SPEC_BITRATE // 1000} kbps",
            abs(m["bit_rate"] - config.SPEC_BITRATE) <= 2000,
        ),
        (
            "Channels",
            f"{m['channels']}",
            f"{config.SPEC_CHANNELS} (mono)",
            m["channels"] == config.SPEC_CHANNELS,
        ),
        (
            "Head silence",
            f"{m['head_silence_s']:.2f} s",
            f"{config.SPEC_HEAD_SILENCE_MIN}-{config.SPEC_HEAD_SILENCE_MAX} s",
            in_range(m["head_silence_s"], config.SPEC_HEAD_SILENCE_MIN, config.SPEC_HEAD_SILENCE_MAX),
        ),
        (
            "Tail silence",
            f"{m['tail_silence_s']:.2f} s",
            f"{config.SPEC_TAIL_SILENCE_MIN}-{config.SPEC_TAIL_SILENCE_MAX} s",
            in_range(m["tail_silence_s"], config.SPEC_TAIL_SILENCE_MIN, config.SPEC_TAIL_SILENCE_MAX),
        ),
    ]
    # Coerce every pass flag to native bool — some comparisons involve numpy
    # scalars whose np.bool_ result is not JSON-serializable downstream.
    specs = [(n, v, r, bool(p)) for n, v, r, p in specs]
    overall_pass = all(s[3] for s in specs)
    return specs, overall_pass
