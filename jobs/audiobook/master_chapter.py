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

import config, ffmpeg_utils, measurements, naming, qc_store, breath, voice_profile


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


def _master_once(trimmed, breath_intervals, params, out_path, tmp, log_lines):
    """Run the full ffmpeg master chain once with the given parameters and
    export to out_path. Deterministic given (trimmed, breath_intervals,
    params) — the retry loop only varies params between calls. Returns
    (measurements, specs, overall_pass)."""
    # Step 4: high-pass (rumble/plosive removal) + afftdn noise reduction,
    # one filtergraph (neither filter is a silenceremove/areverse landmine).
    noise_before = ffmpeg_utils.measure_astats(trimmed, log_lines=log_lines)
    denoised = tmp / "denoised.wav"
    ffmpeg_utils.run([
        "ffmpeg", "-y", "-nostats", "-i", str(trimmed),
        "-af", (
            f"highpass=f={params['highpass_freq']},"
            f"afftdn=nr={params['afftdn_nr']}:nf={params['afftdn_nf']}"
        ),
        str(denoised),
    ], log_lines=log_lines)
    noise_after = ffmpeg_utils.measure_astats(denoised, log_lines=log_lines)
    if noise_before["noise_floor_db"] is not None and noise_after["noise_floor_db"] is not None:
        log_lines.append(
            f"Noise floor (astats est.): {noise_before['noise_floor_db']:.2f} -> "
            f"{noise_after['noise_floor_db']:.2f} dB "
            f"[highpass {params['highpass_freq']}Hz, afftdn nr={params['afftdn_nr']}]"
        )

    # Step 5: de-click / gentle gate (must not strip breaths — breaths are
    # handled explicitly below, not by the gate).
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

    # Step 5b: breath suppression — duck the detected intake breaths so they
    # stop feeding the noise floor. No-op if none were detected.
    breath_filter = breath.suppression_filter(
        breath_intervals, attenuation_db=params["breath_attenuation_db"])
    if breath_filter:
        breathed = tmp / "breathed.wav"
        ffmpeg_utils.run([
            "ffmpeg", "-y", "-nostats", "-i", str(decl),
            "-af", breath_filter,
            str(breathed),
        ], log_lines=log_lines)
        log_lines.append(
            f"Breath suppression: ducked {len(breath_intervals)} interval(s) "
            f"by {params['breath_attenuation_db']:.0f} dB"
        )
    else:
        breathed = decl
        log_lines.append("Breath suppression: no breaths to duck, no-op.")

    # Step 6: mono-downmix check (no-op for a mono source).
    b_info = ffmpeg_utils.probe(breathed)
    if b_info["channels"] > 1:
        log_lines.append(
            f"UNEXPECTED: source has {b_info['channels']} channels "
            "(recording format is mono) — downmixing to mono."
        )
        mono = tmp / "mono.wav"
        ffmpeg_utils.run([
            "ffmpeg", "-y", "-nostats", "-i", str(breathed),
            "-ac", str(config.EXPORT_CHANNELS),
            str(mono),
        ], log_lines=log_lines)
    else:
        mono = breathed

    # Step 7: two-pass loudnorm (targets are per-attempt tunable).
    measured = ffmpeg_utils.loudnorm_measure(
        mono, target_i=params["loudnorm_i"], target_tp=params["true_peak"],
        log_lines=log_lines)
    loud = tmp / "loudnorm.wav"
    ffmpeg_utils.loudnorm_apply(
        mono, loud, measured, target_i=params["loudnorm_i"],
        target_tp=params["true_peak"], log_lines=log_lines)

    # Step 8: add true digital-silence padding, head + tail.
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

    # Step 9: export MP3, forced CBR.
    final_mp3 = tmp / "final.mp3"
    ffmpeg_utils.run([
        "ffmpeg", "-y", "-nostats", "-i", str(padded),
        "-c:a", "libmp3lame", "-b:a", config.EXPORT_BITRATE, "-abr", "0",
        "-ar", str(config.EXPORT_SAMPLE_RATE), "-ac", str(config.EXPORT_CHANNELS),
        str(final_mp3),
    ], log_lines=log_lines)

    # Step 10: copy into processed/ and score.
    shutil.copyfile(final_mp3, out_path)
    m = measurements.measure_file(out_path, log_lines=log_lines)
    specs, overall_pass = measurements.evaluate_specs(m)
    return m, specs, overall_pass


def _adjust_params(params, m, specs, log_lines):
    """Given a failed attempt's measurements + which specs failed, adjust the
    parameters that can actually move the failing metric. Returns
    (changed, adjustments) — changed is False when no failed spec is
    parameter-addressable (or every relevant knob is already maxed), which
    tells the loop to stop early rather than spin."""
    failed = {name for name, _v, _r, passed in specs if not passed}
    adjustments = []
    changed = False

    if "Integrated loudness (RMS)" in failed:
        rms = m["integrated_loudness_db"]
        if rms < config.SPEC_RMS_MIN:
            deficit = config.SPEC_RMS_MIN - rms
            new_i = params["loudnorm_i"] + deficit + 0.3
            adjustments.append(f"RMS {rms:.2f} < {config.SPEC_RMS_MIN}: loudnorm I "
                               f"{params['loudnorm_i']:.2f} -> {new_i:.2f}")
            params["loudnorm_i"] = new_i
            changed = True
        elif rms > config.SPEC_RMS_MAX:
            excess = rms - config.SPEC_RMS_MAX
            new_i = params["loudnorm_i"] - excess - 0.3
            adjustments.append(f"RMS {rms:.2f} > {config.SPEC_RMS_MAX}: loudnorm I "
                               f"{params['loudnorm_i']:.2f} -> {new_i:.2f}")
            params["loudnorm_i"] = new_i
            changed = True

    if "True peak" in failed and m["true_peak_db"] is not None:
        excess = m["true_peak_db"] - config.SPEC_TRUE_PEAK_MAX
        new_tp = params["true_peak"] - excess - 0.5
        adjustments.append(f"True peak {m['true_peak_db']:.2f} > {config.SPEC_TRUE_PEAK_MAX}: "
                           f"loudnorm TP {params['true_peak']:.2f} -> {new_tp:.2f}")
        params["true_peak"] = new_tp
        changed = True

    if "Noise floor" in failed and m["noise_floor_db"] is not None:
        excess = m["noise_floor_db"] - config.SPEC_NOISE_FLOOR_MAX  # dB over the ceiling
        nf_changed = False
        # 1) more aggressive denoise, scaled by how far over we are.
        new_nr = min(config.AFFTDN_NR_MAX, params["afftdn_nr"] + max(4.0, excess * 1.5))
        if new_nr > params["afftdn_nr"]:
            adjustments.append(f"Noise floor {m['noise_floor_db']:.2f} > "
                               f"{config.SPEC_NOISE_FLOOR_MAX}: afftdn nr "
                               f"{params['afftdn_nr']:.1f} -> {new_nr:.1f}")
            params["afftdn_nr"] = new_nr
            nf_changed = True
        # 2) duck breaths harder (they are a prime noise-floor contributor).
        new_att = max(config.BREATH_ATTENUATION_MAX_DB, params["breath_attenuation_db"] - 6.0)
        if new_att < params["breath_attenuation_db"]:
            adjustments.append(f"...and breath attenuation "
                               f"{params['breath_attenuation_db']:.0f} -> {new_att:.0f} dB")
            params["breath_attenuation_db"] = new_att
            nf_changed = True
        # 3) raise the high-pass corner toward its safe ceiling.
        new_hp = min(config.HIGHPASS_FREQ_MAX, params["highpass_freq"] + 10)
        if new_hp > params["highpass_freq"]:
            adjustments.append(f"...and highpass {params['highpass_freq']} -> {new_hp} Hz")
            params["highpass_freq"] = new_hp
            nf_changed = True
        changed = changed or nf_changed

    structural = failed & {"Sample rate", "Bitrate (CBR)", "Channels",
                           "Head silence", "Tail silence"}
    if structural and not changed:
        log_lines.append(
            f"Non-adjustable spec failure(s): {', '.join(sorted(structural))} — "
            "these are fixed export/padding settings, retrying won't help."
        )

    if changed:
        log_lines.append("Retry adjustments: " + "; ".join(adjustments))
    return changed, adjustments


def process_file(input_path, section_number=None, section_title=None,
                  extract_sample=False, sample_start=None, sample_duration=None,
                  narrator=None):
    input_path = Path(input_path)
    narrator = narrator or config.DEFAULT_NARRATOR
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

    out_name = naming.output_filename(section_number, section_title)
    out_path = config.PROCESSED_DIR / out_name
    log_lines = [f"=== {input_path.name} -> section {section_number} "
                 f"'{section_title}' (narrator: {narrator}) ==="]

    with tempfile.TemporaryDirectory(prefix="audiobook_") as tmp:
        tmp = Path(tmp)

        raw_info = ffmpeg_utils.probe(input_path)
        log_lines.append(
            f"Input: {raw_info['sample_rate']}Hz, {raw_info['channels']}ch, "
            f"{raw_info['duration']:.2f}s"
        )

        # Step 2-3: trim head/tail room tone (deterministic — done once, reused
        # by every retry attempt).
        start, end, duration = _trim_bounds(input_path, log_lines)
        log_lines.append(f"Trimming to narration content: {start:.2f}s -> {end:.2f}s "
                          f"(removed {start:.2f}s head, {duration - end:.2f}s tail)")
        trimmed = tmp / "trimmed.wav"
        ffmpeg_utils.run([
            "ffmpeg", "-y", "-nostats", "-i", str(input_path),
            "-af", f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS",
            str(trimmed),
        ], log_lines=log_lines)

        # Breath detection: once, on the trimmed narration, informed by the
        # narrator's learned prior. Intervals are reused across retries; only
        # the ducking depth changes.
        profile = voice_profile.load(narrator)
        prior = voice_profile.breath_prior(profile)
        breath_intervals, breath_stats = breath.detect(
            trimmed, prior=prior, log_lines=log_lines)

        # Seed the first attempt from what has passed for this narrator before.
        params = voice_profile.seed_params(profile)
        log_lines.append(
            f"Seed params (from {profile.get('chapters_passed', 0)} prior "
            f"pass(es)): highpass={params['highpass_freq']}Hz, "
            f"afftdn_nr={params['afftdn_nr']:.1f}, loudnorm_I={params['loudnorm_i']:.2f}, "
            f"breath_att={params['breath_attenuation_db']:.0f}dB"
        )

        all_adjustments = []
        attempt = 0
        m = specs = None
        overall_pass = False
        while attempt < config.MAX_RETRY_ATTEMPTS:
            attempt += 1
            log_lines.append(f"--- Attempt {attempt}/{config.MAX_RETRY_ATTEMPTS} ---")
            m, specs, overall_pass = _master_once(
                trimmed, breath_intervals, params, out_path, tmp, log_lines)
            log_lines.append(
                f"Attempt {attempt} QC: {'PASS' if overall_pass else 'FAIL'} "
                f"(RMS {m['integrated_loudness_db']:.2f}, TP {m['true_peak_db']:.2f}, "
                f"noise floor {m['noise_floor_db']}, "
                f"failed: {[n for n, _v, _r, p in specs if not p] or 'none'})"
            )
            if overall_pass:
                break
            changed, adjustments = _adjust_params(params, m, specs, log_lines)
            all_adjustments.extend(f"[attempt {attempt}] {a}" for a in adjustments)
            if not changed:
                log_lines.append("No further parameter adjustment possible — stopping retries.")
                break

        if not overall_pass:
            log_lines.append(
                f"FLAGGED FOR MANUAL REVIEW after {attempt} attempt(s) — "
                "no parameter set passed all ACX checks."
            )

        # Record QC (with retry metadata) and rewrite the reports.
        final_params = {k: (round(v, 2) if isinstance(v, float) else v)
                        for k, v in params.items()}
        data = qc_store.load_qc_data()
        qc_store.upsert_record(
            data, out_name, section_number, section_title, m, specs, overall_pass,
            retry_attempts=attempt, adjustments=all_adjustments,
            narrator=narrator, breath_count=breath_stats.get("count", 0),
            final_params=final_params)
        qc_store.save_qc_data(data)
        qc_store.write_reports(data)

        # Learn from success only — a failed/flagged chapter never teaches the
        # profile.
        if overall_pass:
            pacing_stats = {"pause_s_mean": breath_stats.get("pause_s_mean"),
                            "speech_ratio": breath_stats.get("speech_ratio")}
            voice_profile.update_after_pass(
                narrator, breath_stats, pacing_stats, params)
            log_lines.append(f"Voice profile updated for narrator '{narrator}'.")

        # Step 11: retail sample extraction, only on the flagged file.
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
    print(f"{input_path.name} -> {out_name}: "
          f"{'PASS' if overall_pass else 'FAIL (manual review)'} "
          f"in {attempt} attempt(s)")
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
    parser.add_argument("--narrator", default=config.DEFAULT_NARRATOR,
                         help="Narrator key for the persistent voice profile "
                              f"(default: {config.DEFAULT_NARRATOR})")
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
            narrator=args.narrator,
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
            narrator=args.narrator,
        )


if __name__ == "__main__":
    main()
