from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import tracemalloc

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reviewer_pipeline.data import (
    add_communication_scenario,
    bootstrap_synthetic_workload,
    job_size_categories,
    load_json,
    load_real_subset,
    sample_jobs_from_pool,
)
from reviewer_pipeline.dpm import replay_with_common_dpm
from reviewer_pipeline.schedulers import get_scheduler


def build_catalog(real_workload, config, target_tasks: int):
    categories = job_size_categories(real_workload)
    seeds = config["random_seeds"]
    communication_scenarios = list(config["communication"]["scenario_medians_mb"])
    arrival_scales = {"light": 1.75, "medium": 1.0, "heavy": 0.5}

    # 27 partial real-replay workloads: 3 DAG-size classes x 3 communication x 3 seeds.
    for size_name, job_pool in categories.items():
        for communication in communication_scenarios:
            for seed in seeds:
                sampled = sample_jobs_from_pool(
                    real_workload,
                    job_pool,
                    target_task_count=target_tasks,
                    seed=seed,
                    arrival_scale=1.0,
                    workload_id=f"real_{size_name}_{communication}_s{seed}",
                )
                yield add_communication_scenario(sampled, communication, seed, config), "partial_real", size_name, "observed", communication, seed

    # 81 synthetic workloads: 3 DAG-size classes x 3 arrival x 3 communication x 3 seeds.
    for size_name, job_pool in categories.items():
        source = sample_jobs_from_pool(
            real_workload,
            job_pool,
            target_task_count=max(target_tasks * 2, target_tasks + 10),
            seed=seeds[0],
            arrival_scale=1.0,
            workload_id=f"source_{size_name}",
        )
        source_job_count = len({task.job_id for task in source.tasks.values()})
        for arrival_name, arrival_scale in arrival_scales.items():
            for communication in communication_scenarios:
                for seed in seeds:
                    synthetic = bootstrap_synthetic_workload(
                        source,
                        number_of_jobs=source_job_count,
                        seed=seed,
                        arrival_scale=arrival_scale,
                        workload_id=f"synthetic_{size_name}_{arrival_name}_{communication}_s{seed}",
                    )
                    synthetic = add_communication_scenario(synthetic, communication, seed, config)
                    yield synthetic, "synthetic", size_name, arrival_name, communication, seed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run HEFT, EADAGS-adapted, and Proposed with a common DPM controller.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "experiment_config.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "common_dpm")
    parser.add_argument("--target-tasks", type=int, default=100, help="Approximate complete-job task count per workload.")
    parser.add_argument("--schedulers", nargs="+", default=["HEFT", "EADAGS_adapted", "Proposed"])
    parser.add_argument("--limit-workloads", type=int, default=None, help="Useful for smoke tests only.")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    schedule_dir = args.output_dir / "schedules"
    timeline_dir = args.output_dir / "timelines"
    machine_dir = args.output_dir / "machine_summaries"
    for directory in [schedule_dir, timeline_dir, machine_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    config = load_json(args.config)
    real_workload = load_real_subset(args.data_dir, config)
    catalog = list(build_catalog(real_workload, config, args.target_tasks))
    if args.limit_workloads is not None:
        catalog = catalog[: args.limit_workloads]

    result_rows = []
    manifest_rows = []
    for workload_index, (workload, family, dag_size, arrival, communication, seed) in enumerate(catalog, start=1):
        print(f"[{workload_index}/{len(catalog)}] {workload.workload_id}: {len(workload.tasks)} tasks, {len(workload.machines)} machines")
        for scheduler_name in args.schedulers:
            scheduler = get_scheduler(scheduler_name, config)
            tracemalloc.start()
            started = time.perf_counter()
            schedule = scheduler.schedule(workload)
            scheduling_runtime = time.perf_counter() - started
            _, peak_memory = tracemalloc.get_traced_memory()
            tracemalloc.stop()

            safe_scheduler = scheduler.name.replace(" ", "_")
            schedule_path = schedule_dir / f"{workload.workload_id}__{safe_scheduler}.csv"
            schedule.rows.to_csv(schedule_path, index=False)

            for dpm_enabled in [False, True]:
                replay = replay_with_common_dpm(workload, schedule, config, dpm_enabled=dpm_enabled)
                variant = f"{scheduler.name}_{'DPM' if dpm_enabled else 'NoDPM'}"
                timeline_path = timeline_dir / f"{workload.workload_id}__{variant}.csv"
                machine_path = machine_dir / f"{workload.workload_id}__{variant}.csv"
                replay.task_timeline.to_csv(timeline_path, index=False)
                replay.machine_summary.to_csv(machine_path, index=False)

                record = dict(replay.metrics)
                record.update(
                    {
                        "variant": variant,
                        "family": family,
                        "dag_size": dag_size,
                        "arrival_condition": arrival,
                        "communication_condition": communication,
                        "seed": seed,
                        "scheduling_runtime_s": scheduling_runtime,
                        "peak_scheduler_memory_bytes": peak_memory,
                        "candidate_evaluations": scheduler.candidate_evaluations,
                        "schedule_file": str(schedule_path),
                        "timeline_file": str(timeline_path),
                        "machine_summary_file": str(machine_path),
                    }
                )
                result_rows.append(record)

        manifest_rows.append(
            {
                "workload_id": workload.workload_id,
                "family": family,
                "dag_size": dag_size,
                "arrival_condition": arrival,
                "communication_condition": communication,
                "seed": seed,
                "task_count": len(workload.tasks),
                "edge_count": len(workload.edges),
                "machine_count": len(workload.machines),
            }
        )

    pd.DataFrame(result_rows).to_csv(args.output_dir / "common_dpm_results.csv", index=False)
    pd.DataFrame(manifest_rows).to_csv(args.output_dir / "workload_manifest.csv", index=False)
    (args.output_dir / "run_configuration.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"Wrote {len(result_rows)} result rows to {args.output_dir / 'common_dpm_results.csv'}")


if __name__ == "__main__":
    main()
