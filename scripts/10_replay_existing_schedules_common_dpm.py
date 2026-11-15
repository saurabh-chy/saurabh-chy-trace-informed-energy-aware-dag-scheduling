from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reviewer_pipeline.data import add_communication_scenario, load_json, load_real_subset
from reviewer_pipeline.dpm import replay_with_common_dpm


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply the common DPM controller to existing scheduler assignment CSVs without reimplementing the schedulers."
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--schedule-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "experiment_config.json")
    parser.add_argument("--communication", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timeline_dir = args.output_dir / "timelines"
    machine_dir = args.output_dir / "machine_summaries"
    timeline_dir.mkdir(exist_ok=True)
    machine_dir.mkdir(exist_ok=True)

    config = load_json(args.config)
    workload = add_communication_scenario(
        load_real_subset(args.data_dir, config), args.communication, args.seed, config
    )

    result_rows = []
    for schedule_path in sorted(args.schedule_dir.glob("*.csv")):
        schedule = pd.read_csv(schedule_path)
        if "task_key" not in schedule.columns:
            raise ValueError(
                f"{schedule_path.name} must contain task_key. Required schedule columns are documented in README.md."
            )
        for enabled in [False, True]:
            replay = replay_with_common_dpm(workload, schedule, config, dpm_enabled=enabled)
            variant = f"{replay.scheduler}_{'DPM' if enabled else 'NoDPM'}"
            replay.task_timeline.to_csv(timeline_dir / f"{schedule_path.stem}__{variant}.csv", index=False)
            replay.machine_summary.to_csv(machine_dir / f"{schedule_path.stem}__{variant}.csv", index=False)
            record = dict(replay.metrics)
            record["variant"] = variant
            record["source_schedule"] = str(schedule_path)
            result_rows.append(record)

    pd.DataFrame(result_rows).to_csv(args.output_dir / "existing_schedule_common_dpm_results.csv", index=False)
    print(f"Processed {len(result_rows)} schedule-mode combinations.")


if __name__ == "__main__":
    main()
