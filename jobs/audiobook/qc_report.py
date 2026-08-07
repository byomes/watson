"""Regenerate the QC report from scratch by re-measuring every MP3 in
processed/. Use this if reports/qc_data.json is stale, missing, or you
just want a from-scratch re-check independent of whatever master_chapter.py
last recorded.

    python qc_report.py --regenerate
"""

import argparse

import config, measurements, naming, qc_store


def regenerate():
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    mp3_files = sorted(config.PROCESSED_DIR.glob("*.mp3"))
    if not mp3_files:
        print(f"No MP3s found in {config.PROCESSED_DIR}")
        return

    for mp3_path in mp3_files:
        section_number, section_title = naming.parse_output_filename(mp3_path.name)
        print(f"Measuring {mp3_path.name} ...")
        m = measurements.measure_file(mp3_path)
        specs, overall_pass = measurements.evaluate_specs(m)
        qc_store.upsert_record(
            data, mp3_path.name, section_number, section_title, m, specs, overall_pass
        )

    qc_store.save_qc_data(data)
    qc_store.write_reports(data)
    print(f"Wrote {config.QC_REPORT_MD} and {config.QC_REPORT_CSV}")


def main():
    parser = argparse.ArgumentParser(description="ACX audiobook QC report")
    parser.add_argument("--regenerate", action="store_true",
                         help="Re-measure every MP3 in processed/ and rewrite the report")
    args = parser.parse_args()

    if args.regenerate:
        regenerate()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
