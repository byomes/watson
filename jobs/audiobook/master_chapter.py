"""ACX audiobook mastering pipeline — one chapter/section per invocation.

    python master_chapter.py --input input/01_Introduction-raw.wav
    python master_chapter.py --all
    python master_chapter.py --input input/01_Introduction-raw.wav --sample
    python master_chapter.py --input input/01_Introduction-raw.wav --sample \\
        --sample-start 00:00:00 --sample-duration 300

Input files are named <section#>_<SectionTitle>[-raw].wav — section number
and title are parsed from the filename, no per-file flags required for
--all. Opening/closing credits are just another section recorded and
processed the same way, differentiated only by filename.

Steps 2-10 are idempotent per input file: dropping a replacement raw file
in and re-running with the same input reprocesses just that section.
"""

import argparse
import shutil
import tempfile
from pathlib import Path

import config, ffmpeg_utils, measurements, naming, qc_store


def _parse_timecode(value):
    """Accepts either raw seconds ('300') or HH:MM:SS ('00:05:00')."""
    if ":" not in value:
        return float(value)
    parts = [float(p) for p in value.split(":")]
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _trim_bounds(input_path, log_lines):
    """Detects existing head/tail room tone and returns (start, end)
    seconds to trim the file to narration-only content."""
    info = ffmpeg_utils.probe(input_path)
    duration = info["duration"]
    silences = ffmpeg_utils.detect_silence(input_path, log_lines=log_lines)

    start = 0.0
    end = duration
    if silences:
        first_start, first_end = silences[0]
        if first_start <= 0.05:
            start = first_end
        last_start, last_end = silences[-1]
        if duration and (duration - last_end) <= 0.05:
            end = last_start
    return start, end, duration


def process_file(input_path, section_number=None, section_title=None,
                  extract_sample=False, sample_start=None, sample_duration=None):
    input_path = Path(input_path)
    if section_number is None or section_title is None:
        parsed_number, parsed_title = naming.parse_input_filename(input_path)
        section_number = section_number if section_number is not None else parsed_number
        section_title = section_title or parsed_title
    if section_number is None or not section_title:
        raise SystemExit(
            f"Could not determine section number/title for {input_path.name}. "
            "Name it <section#>_<SectionTitle>-raw.wav or pass "
            "--section-number/--section-title explicitly."
        )

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    config.SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    log_lines = [f"=== {input_path.name} -> section {section_number} '{section_title}' ==="]

    with tempfile.TemporaryDirectory(prefix="audiobook_") as tmp:
        tmp = Path(tmp)

        raw_info = ffmpeg_utils.probe(input_path)
        log_lines.append(
            f"Input: {raw_info['sample_rate']}Hz, {raw_info['channels']}ch, "
            f"{raw_info['duration']:.2f}s"
        )

        # Step 2-3: detect + trim head/tail room tone
        start, end, duration = _trim_bounds(input_path, log_lines)
        log_lines.append(f"Trimming to narration content: {start:.2f}s -> {end:.2f}s "
                          f"(removed {start:.2f}s head, {duration - end:.2f}s tail)")
        trimmed = tmp / "trimmed.wav"
        ffmpeg_utils.run([
            "ffmpeg", "-y", "-nostats", "-i", str(input_path),
            "-af", f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS",
            str(trimmed),
        ], log_lines=log_lines)

        # Step 4: noise reduction, measured before/after
        noise_before = ffmpeg_utils.measure_astats(trimmed, log_lines=log_lines)
        denoised = tmp / "denoised.wav"
        ffmpeg_utils.run([
            "ffmpeg", "-y", "-nostats", "-i", str(trimmed),
            "-af", f"afftdn=nr={config.AFFTDN_NR}:nf={config.AFFTDN_NF}",
            str(denoised),
        ], log_lines=log_lines)
        noise_after = ffmpeg_utils.measure_astats(denoised, log_lines=log_lines)
        if noise_before["noise_floor_db"] is not None and noise_after["noise_floor_db"] is not None:
            log_lines.append(
                f"Noise floor: {noise_before['noise_floor_db']:.2f} dB -> "
                f"{noise_after['noise_floor_db']:.2f} dB "
                f"(delta {noise_after['noise_floor_db'] - noise_before['noise_floor_db']:+.2f} dB) "
                "[afftdn settings are moderate defaults, need real-chapter tuning]"
            )

        # Step 5: de-click / breath-taming (moderate — must not strip breaths)
        decl = tmp / "declicked.wav"
        ffmpeg_utils.run([
            "ffmpeg", "-y", "-nostats", "-i", str(denoised),
            "-af", (
                f"adeclick=threshold={config.ADECLICK_THRESHOLD}:burst={config.ADECLICK_BURST},"
                f"agate=threshold={config.AGATE_THRESHOLD_DB}dB:ratio={config.AGATE_RATIO}:"
                f"attack={config.AGATE_ATTACK_MS}:release={config.AGATE_RELEASE_MS}"
            ),
            str(decl),
        ], log_lines=log_lines)

        # Step 6: mono-downmix check — should be a no-op given the mono
        # recording format; only a real conversion (and log entry) if a
        # stray stereo file comes through.
        decl_info = ffmpeg_utils.probe(decl)
        if decl_info["channels"] > 1:
            log_lines.append(
                f"UNEXPECTED: source has {decl_info['channels']} channels "
                "(recording format is mono) — downmixing to mono."
            )
            mono = tmp / "mono.wav"
            ffmpeg_utils.run([
                "ffmpeg", "-y", "-nostats", "-i", str(decl),
                "-ac", str(config.EXPORT_CHANNELS),
                str(mono),
            ], log_lines=log_lines)
        else:
            log_lines.append("Mono-downmix check: source already mono, no-op.")
            mono = decl

        # Step 7: two-pass loudnorm
        measured = ffmpeg_utils.loudnorm_measure(mono, log_lines=log_lines)
        loud = tmp / "loudnorm.wav"
        ffmpeg_utils.loudnorm_apply(mono, loud, measured, log_lines=log_lines)

        # Step 8: add true digital silence padding, head + tail
        padded = tmp / "padded.wav"
        ffmpeg_utils.run([
            "ffmpeg", "-y", "-nostats",
            "-f", "lavfi", "-i",
            f"anullsrc=r={config.EXPORT_SAMPLE_RATE}:cl=mono:d={config.HEAD_PAD_SECONDS}",
            "-i", str(loud),
            "-f", "lavfi", "-i",
            f"anullsrc=r={config.EXPORT_SAMPLE_RATE}:cl=mono:d={config.TAIL_PAD_SECONDS}",
            "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]",
            "-map", "[out]",
            str(padded),
        ], log_lines=log_lines)

        # Step 9: export MP3, forced CBR
        final_mp3 = tmp / "final.mp3"
        ffmpeg_utils.run([
            "ffmpeg", "-y", "-nostats", "-i", str(padded),
            "-c:a", "libmp3lame", "-b:a", config.EXPORT_BITRATE, "-abr", "0",
            "-ar", str(config.EXPORT_SAMPLE_RATE), "-ac", str(config.EXPORT_CHANNELS),
            str(final_mp3),
        ], log_lines=log_lines)

        # Step 10: rename into processed/
        out_name = naming.output_filename(section_number, section_title)
        out_path = config.PROCESSED_DIR / out_name
        shutil.copyfile(final_mp3, out_path)
        log_lines.append(f"Exported: {out_path}")

        # QC measurement runs against the final exported MP3 only
        m = measurements.measure_file(out_path, log_lines=log_lines)
        specs, overall_pass = measurements.evaluate_specs(m)
        data = qc_store.load_qc_data()
        qc_store.upsert_record(data, out_name, section_number, section_title, m, specs, overall_pass)
        qc_store.save_qc_data(data)
        qc_store.write_reports(data)
        log_lines.append(f"QC: {'PASS' if overall_pass else 'FAIL'}")

        # Step 11: retail sample extraction, only on the flagged file
        if extract_sample:
            start_s = sample_start if sample_start is not None else config.SAMPLE_DEFAULT_START_SECONDS
            dur_s = sample_duration if sample_duration is not None else config.SAMPLE_DEFAULT_DURATION_SECONDS
            sample_name = f"{config.FILENAME_PREFIX}_Sample_{section_title}.mp3"
            sample_path = config.SAMPLES_DIR / sample_name
            ffmpeg_utils.run([
                "ffmpeg", "-y", "-nostats", "-ss", str(start_s), "-t", str(dur_s),
                "-i", str(out_path),
                "-c:a", "libmp3lame", "-b:a", config.EXPORT_BITRATE, "-abr", "0",
                str(sample_path),
            ], log_lines=log_lines)
            log_lines.append(f"Retail sample: {sample_path} ({start_s}s + {dur_s}s)")

    log_path = config.LOGS_DIR / f"{input_path.stem}.log"
    log_path.write_text("\n".join(log_lines) + "\n")
    print(f"{input_path.name} -> {out_name}: {'PASS' if overall_pass else 'FAIL'}")
    return out_path, overall_pass


def main():
    parser = argparse.ArgumentParser(description="ACX audiobook mastering pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", help="Path to a single raw WAV file")
    group.add_argument("--all", action="store_true", help="Process every WAV in input/")
    parser.add_argument("--section-number", type=int, default=None,
                         help="Override section number (default: parsed from filename)")
    parser.add_argument("--section-title", default=None,
                         help="Override section title (default: parsed from filename)")
    parser.add_argument("--sample", action="store_true",
                         help="Also extract a retail sample (default: first 5 min)")
    parser.add_argument("--sample-start", default=None,
                         help="Manual override: sample start (seconds or HH:MM:SS)")
    parser.add_argument("--sample-duration", default=None,
                         help="Manual override: sample duration in seconds")
    args = parser.parse_args()

    sample_start = _parse_timecode(args.sample_start) if args.sample_start else None
    sample_duration = float(args.sample_duration) if args.sample_duration else None

    if args.input:
        process_file(
            args.input,
            section_number=args.section_number,
            section_title=args.section_title,
            extract_sample=args.sample,
            sample_start=sample_start,
            sample_duration=sample_duration,
        )
        return

    inputs = sorted(config.INPUT_DIR.glob("*.wav"))
    if not inputs:
        print(f"No WAV files found in {config.INPUT_DIR}")
        return

    parsed = [(p, *naming.parse_input_filename(p)) for p in inputs]
    chapter_one = next((p for p, num, _ in parsed if num == 1), None)

    for path, number, title in parsed:
        is_sample_target = args.sample and (chapter_one is None or path == chapter_one)
        process_file(
            path,
            section_number=number,
            section_title=title,
            extract_sample=is_sample_target,
            sample_start=sample_start,
            sample_duration=sample_duration,
        )


if __name__ == "__main__":
    main()
