"""Filename conventions for the pipeline.

Input convention: raw files in input/ are named
    <section#>_<SectionTitle>[-raw].wav
e.g. 01_Introduction-raw.wav, 12_ClosingCredits-raw.wav. Section number and
title are parsed straight from the filename — no per-file CLI flags needed
for --all batch runs. Credit files are just another section: same pattern,
no special-cased naming or logic.

Output convention: processed/Yomes_WJ_<section#, zero-padded>_<Title>.mp3
"""

import re

import config

INPUT_PATTERN = re.compile(r"^(\d+)[_-]+(.+?)(?:[_-]raw)?$", re.IGNORECASE)
OUTPUT_PATTERN = re.compile(
    re.escape(config.FILENAME_PREFIX) + r"_(\d+)_(.+)\.mp3$"
)


def _sanitize_title(raw_title):
    return re.sub(r"[^A-Za-z0-9]", "", raw_title)


def parse_input_filename(path):
    """Returns (section_number, section_title) parsed from a raw input
    filename, or (None, None) if it doesn't match the convention."""
    stem = path.stem
    match = INPUT_PATTERN.match(stem)
    if not match:
        return None, None
    number = int(match.group(1))
    title = _sanitize_title(match.group(2))
    return number, title


def parse_output_filename(filename):
    """Returns (section_number, section_title) parsed back out of an
    already-exported Yomes_WJ_NN_Title.mp3 filename."""
    match = OUTPUT_PATTERN.match(filename)
    if not match:
        return None, None
    return int(match.group(1)), match.group(2)


def output_filename(section_number, section_title):
    return f"{config.FILENAME_PREFIX}_{section_number:02d}_{section_title}.mp3"
