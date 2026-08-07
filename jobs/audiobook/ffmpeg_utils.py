"""Thin wrappers around ffmpeg/ffprobe CLI calls. No audio processing
happens in Python — ffmpeg does all the work, this just shells out and
parses stderr/stdout.
"""

import json
import re
import subprocess

import config


class FfmpegError(RuntimeError):
    pass


def run(args, log_lines=None):
    """Run an ffmpeg/ffprobe command, return (stdout, stderr). Raises
    FfmpegError on non-zero exit. Appends the full command + stderr to
    log_lines if provided (caller writes it to the per-file log file)."""
    if args and args[0] == "ffmpeg" and "-hide_banner" not in args:
        args = [args[0], "-hide_banner"] + args[1:]
    proc = subprocess.run(args, capture_output=True, text=True)
    if log_lines is not None:
        log_lines.append(f"$ {' '.join(args)}")
        log_lines.append(proc.stderr)
    if proc.returncode != 0:
        raise FfmpegError(
            f"Command failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr}"
        )
    return proc.stdout, proc.stderr


def probe(path):
    """ffprobe format+stream info as a dict: sample_rate, channels,
    duration, bit_rate, codec_name."""
    out, _ = run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    data = json.loads(out)
    audio_stream = next(s for s in data["streams"] if s["codec_type"] == "audio")
    fmt = data["format"]
    return {
        "sample_rate": int(audio_stream.get("sample_rate", 0)),
        "channels": int(audio_stream.get("channels", 0)),
        "codec_name": audio_stream.get("codec_name"),
        "duration": float(fmt.get("duration", audio_stream.get("duration", 0)) or 0),
        "bit_rate": int(fmt.get("bit_rate", audio_stream.get("bit_rate", 0)) or 0),
    }


def detect_silence(path, noise_db=None, min_duration=None, log_lines=None):
    """Runs silencedetect over the whole file, returns list of
    (start, end) tuples in seconds for every detected silent interval."""
    noise_db = config.SILENCE_NOISE_DB if noise_db is None else noise_db
    min_duration = config.SILENCE_MIN_DURATION if min_duration is None else min_duration
    _, err = run([
        "ffmpeg", "-nostats", "-i", str(path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ], log_lines=log_lines)

    starts = [float(m) for m in re.findall(r"silence_start:\s*(-?[\d.]+)", err)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*(-?[\d.]+)", err)]
    # silencedetect always pairs a start with an end unless the file ends
    # while still silent, in which case the trailing start has no matching
    # end — drop it rather than guess.
    return list(zip(starts, ends))


def measure_astats(path, log_lines=None):
    """Returns dict with rms_db, peak_db, noise_floor_db from the astats
    filter (Overall channel block)."""
    _, err = run([
        "ffmpeg", "-nostats", "-i", str(path),
        "-af", "astats=metadata=0:reset=0",
        "-f", "null", "-",
    ], log_lines=log_lines)

    def last_overall(pattern):
        matches = re.findall(pattern, err)
        if not matches:
            return None
        return float(matches[-1])

    # astats reports -inf/inf literally (e.g. true digital silence has no
    # measurable noise floor at all) — Python's float() parses these natively,
    # the regex just needs to allow the letters through.
    value_re = r"(-?(?:[\d.]+|inf))"
    return {
        "rms_db": last_overall(r"RMS level dB:\s*" + value_re),
        "peak_db": last_overall(r"Peak level dB:\s*" + value_re),
        "noise_floor_db": last_overall(r"Noise floor dB:\s*" + value_re),
    }


def loudnorm_measure(path, log_lines=None):
    """Pass 1 of two-pass loudnorm — measures the file, returns the JSON
    stats block ffmpeg prints to stderr."""
    _, err = run([
        "ffmpeg", "-nostats", "-i", str(path),
        "-af", (
            f"loudnorm=I={config.TARGET_INTEGRATED_LOUDNESS}:"
            f"TP={config.TARGET_TRUE_PEAK}:LRA={config.TARGET_LRA}:print_format=json"
        ),
        "-f", "null", "-",
    ], log_lines=log_lines)

    match = re.search(r"\{[^{}]*\}", err, re.DOTALL)
    if not match:
        raise FfmpegError(f"Could not parse loudnorm measurement output:\n{err}")
    return json.loads(match.group(0))


def loudnorm_apply(input_path, output_path, measured, log_lines=None):
    """Pass 2 of two-pass loudnorm — applies normalization using the
    measured stats from pass 1 (linear mode, per ffmpeg's own recommended
    two-pass recipe)."""
    filt = (
        f"loudnorm=I={config.TARGET_INTEGRATED_LOUDNESS}:"
        f"TP={config.TARGET_TRUE_PEAK}:LRA={config.TARGET_LRA}:"
        f"measured_I={measured['input_i']}:"
        f"measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:"
        f"measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:"
        f"linear=true:print_format=json"
    )
    run([
        "ffmpeg", "-y", "-nostats", "-i", str(input_path),
        "-af", filt,
        "-ar", str(config.EXPORT_SAMPLE_RATE),
        "-ac", str(config.EXPORT_CHANNELS),
        str(output_path),
    ], log_lines=log_lines)
