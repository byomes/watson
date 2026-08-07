"""Persistent, per-narrator voice profile for the ACX mastering pipeline.

This is *narrator* state, not book state — it lives at
data/audiobook_voice_profiles.json (repo-wide data/, same home as the other
JSON stores) and carries across books. It records, per narrator:

  - breath acoustics (typical amplitude band + spectral centroid) — used as a
    PRIOR to narrow breath detection on the next chapter
  - pacing / pause statistics
  - which master parameters actually produced a PASS on past chapters — used to
    SEED the first mastering attempt so later chapters need fewer retries

Every learned scalar is stored as a running {"mean", "n"} pair so a single bad
chapter can't yank the prior around. The profile is updated ONLY after a
chapter passes QC (see master_chapter.process_file) — a failed/manual-review
chapter never teaches the profile.
"""

import json
from datetime import datetime, timezone

import config


def _blank_profile():
    return {"chapters_passed": 0, "breath": {}, "pacing": {}, "params": {},
            "updated_at": None}


def load_all():
    path = config.VOICE_PROFILE_PATH
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def load(narrator=None):
    narrator = narrator or config.DEFAULT_NARRATOR
    return load_all().get(narrator, _blank_profile())


def save(narrator, profile):
    narrator = narrator or config.DEFAULT_NARRATOR
    path = config.VOICE_PROFILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load_all()
    data[narrator] = profile
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def _mean(bucket, key, default):
    entry = bucket.get(key)
    if not entry or entry.get("n", 0) <= 0:
        return default
    return entry["mean"]


def _fold(bucket, key, value):
    """Incrementally fold one observation into a running {"mean","n"} entry."""
    if value is None:
        return
    entry = bucket.setdefault(key, {"mean": 0.0, "n": 0})
    entry["n"] += 1
    entry["mean"] += (value - entry["mean"]) / entry["n"]


def breath_prior(profile):
    """Breath-detection thresholds, tightened by the learned prior when one
    exists. With no history we fall back to the wide config defaults."""
    b = profile.get("breath", {})
    amp_mean = _mean(b, "amp_db_mean", None)
    centroid_mean = _mean(b, "centroid_hz_mean", None)

    rms_floor = config.BREATH_RMS_FLOOR_DB
    rms_ceil = config.BREATH_RMS_CEIL_DB
    centroid_max = config.BREATH_CENTROID_MAX_HZ
    if amp_mean is not None:
        # Center the amplitude band on the narrator's learned breath level,
        # but never wider than the config defaults.
        rms_floor = max(config.BREATH_RMS_FLOOR_DB, amp_mean - 12.0)
        rms_ceil = min(config.BREATH_RMS_CEIL_DB, amp_mean + 12.0)
    if centroid_mean is not None:
        centroid_max = min(config.BREATH_CENTROID_MAX_HZ, centroid_mean + 600.0)
    return {"rms_floor_db": rms_floor, "rms_ceil_db": rms_ceil,
            "centroid_max_hz": centroid_max}


def seed_params(profile):
    """First-attempt master parameters, seeded from what has passed before.
    Falls back to config defaults with no history."""
    p = profile.get("params", {})
    return {
        "highpass_freq": round(_mean(p, "highpass_freq", config.HIGHPASS_FREQ)),
        "afftdn_nr": _mean(p, "afftdn_nr", config.AFFTDN_NR),
        "afftdn_nf": config.AFFTDN_NF,
        "loudnorm_i": _mean(p, "loudnorm_i", config.TARGET_INTEGRATED_LOUDNESS),
        "true_peak": config.TARGET_TRUE_PEAK,
        "breath_attenuation_db": _mean(
            p, "breath_attenuation_db", config.BREATH_ATTENUATION_DB),
    }


def update_after_pass(narrator, breath_stats, pacing_stats, final_params):
    """Fold a passing chapter's acoustics + winning parameters into the
    profile. Called ONLY on QC PASS."""
    narrator = narrator or config.DEFAULT_NARRATOR
    profile = load(narrator)
    profile["chapters_passed"] = profile.get("chapters_passed", 0) + 1

    b = profile.setdefault("breath", {})
    if breath_stats:
        _fold(b, "amp_db_mean", breath_stats.get("amp_db_mean"))
        _fold(b, "centroid_hz_mean", breath_stats.get("centroid_hz_mean"))
        _fold(b, "count_per_min", breath_stats.get("count_per_min"))

    pc = profile.setdefault("pacing", {})
    if pacing_stats:
        _fold(pc, "pause_s_mean", pacing_stats.get("pause_s_mean"))
        _fold(pc, "speech_ratio", pacing_stats.get("speech_ratio"))

    p = profile.setdefault("params", {})
    _fold(p, "highpass_freq", final_params.get("highpass_freq"))
    _fold(p, "afftdn_nr", final_params.get("afftdn_nr"))
    _fold(p, "loudnorm_i", final_params.get("loudnorm_i"))
    _fold(p, "breath_attenuation_db", final_params.get("breath_attenuation_db"))

    profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    save(narrator, profile)
    return profile
