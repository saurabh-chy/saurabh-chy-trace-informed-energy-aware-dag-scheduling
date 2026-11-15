from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
import tracemalloc

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reviewer_pipeline.data import (
    add_communication_scenario,
    load_json,
    load_real_subset,
    select_complete_jobs,
)
from reviewer_pipeline.dpm import replay_with_common_dpm
from reviewer_pipeline.schedulers import get_scheduler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run reviewer-requested scalability experiments up to 1,000 tasks.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "experiment_config.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "scalability")
    parser.add_argument(
        "--schedulers",
        nargs="+",
        default=["FCFS", "RoundRobin", "SJF", "MinMin", "HEFT", "EADAGS_adapted", "Proposed"],
    )
    parser.add_argument("--communication", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--arrival-compression", type=float, default=None,
                        help="Override config scalability.arrival_compression_factor")
    parser.add_argument("--target-task-counts", type=int, nargs="+", default=None)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    config = load_json(args.config)
    base = load_real_subset(args.data_dir, config)
    scalability = config["scalability"]
    target_counts = args.target_task_counts or [int(value) for value in scalability["target_task_counts"]]
    machine_counts = [int(value) for value in scalability["machine_counts"]]
    seeds = [int(value) for value in scalability["seeds"]]
    arrival_compression = (
        args.arrival_compression
        if args.arrival_compression is not None
        else float(scalability.get("arrival_compression_factor", 0.01))
    )

    rows = []
    rng = np.random.default_rng(2026)
    all_machine_ids = np.array(sorted(base.machines))

    for target in target_counts:
        for seed in seeds:
            workload = select_complete_jobs(
                base,
                target_task_count=target,
                seed=seed,
                arrival_compression_factor=arrival_compression,
            )
            workload = add_communication_scenario(workload, args.communication, seed, config)
            for machine_count in machine_counts:
                if machine_count > len(all_machine_ids):
                    continue
                machine_ids = rng.choice(all_machine_ids, size=machine_count, replace=False).tolist()
                scaled = workload.with_machine_subset(
                    machine_ids,
                    workload_id=f"scale_target{target}_realised{len(workload.tasks)}_m{machine_count}_s{seed}",
                )
                print(f"Running {scaled.workload_id}")
                for scheduler_name in args.schedulers:
                    scheduler = get_scheduler(scheduler_name, config)
                    tracemalloc.start()
                    started = time.perf_counter()
                    schedule = scheduler.schedule(scaled)
                    runtime = time.perf_counter() - started
                    _, peak_memory = tracemalloc.get_traced_memory()
                    tracemalloc.stop()

                    # Use the common DPM controller for every scheduler. This is the
                    # fair package-level scalability comparison requested by reviewers.
                    replay = replay_with_common_dpm(scaled, schedule, config, dpm_enabled=True)
                    record = dict(replay.metrics)
                    record.update(
                        {
                            "target_task_count": target,
                            "realised_task_count": len(scaled.tasks),
                            "scheduler_runtime_s": runtime,
                            "peak_scheduler_memory_bytes": peak_memory,
                            "candidate_evaluations": scheduler.candidate_evaluations,
                            "seed": seed,
                            "communication_condition": args.communication,
                            "variant": f"{scheduler.name}_DPM",
                        }
                    )
                    rows.append(record)

                    pd.DataFrame(rows).to_csv(args.output_dir / "scalability_results_partial.csv", index=False)

    pd.DataFrame(rows).to_csv(args.output_dir / "scalability_results.csv", index=False)
    print(f"Scalability results written to {args.output_dir / 'scalability_results.csv'}")


if __name__ == "__main__":
    main()
