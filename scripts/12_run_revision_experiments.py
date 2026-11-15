"""Revision (round 2) experiments: heterogeneity, Proposed-v2, realistic utilisation.

Reuses the 108-workload common-DPM catalogue from script 02 and the common DPM
replay, so every scheduler is evaluated with the same power-management controller.

Settings are combined with flags:
  --hetero                 assign three modelled server classes (legacy / baseline / efficient)
  --machines N             use a seeded subset of N machines (default: all 200)
  --arrival-compression c  multiply every release time by c (c < 1 raises utilisation)

Results are written one CSV per workload so that long runs can be resumed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from dataclasses import replace
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reviewer_pipeline.data import Workload, load_json, load_real_subset
from reviewer_pipeline.dpm import replay_with_common_dpm
from reviewer_pipeline.schedulers import get_scheduler

_spec = importlib.util.spec_from_file_location("run02", ROOT / "scripts" / "02_run_common_dpm_baselines.py")
_run02 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_run02)
build_catalog = _run02.build_catalog

# Modelled server generations. Values are assumptions in the spirit of published
# SPECpower_ssj2008 trends (newer servers: higher throughput, lower idle power and
# better energy proportionality); they are NOT measurements and must be labelled
# as modelled in the manuscript.
MACHINE_CLASSES = {
    "legacy": {"speed_multiplier": 0.80, "idle_power_w": 120.0, "peak_power_w": 250.0, "sleep_power_w": 15.0},
    "baseline": {"speed_multiplier": 1.00, "idle_power_w": 99.0, "peak_power_w": 220.0, "sleep_power_w": 11.0},
    "efficient": {"speed_multiplier": 1.25, "idle_power_w": 65.0, "peak_power_w": 230.0, "sleep_power_w": 7.0},
}


def apply_heterogeneity(machines: dict) -> dict:
    classes = list(MACHINE_CLASSES)
    result = {}
    for index, machine_id in enumerate(sorted(machines)):
        name = classes[index % len(classes)]
        profile = MACHINE_CLASSES[name]
        machine = machines[machine_id]
        result[machine_id] = replace(
            machine,
            speed_factor=machine.speed_factor * profile["speed_multiplier"],
            machine_class=name,
            idle_power_w=profile["idle_power_w"],
            peak_power_w=profile["peak_power_w"],
            sleep_power_w=profile["sleep_power_w"],
        )
    return result


def transform(workload: Workload, machines: dict, compression: float) -> Workload:
    tasks = workload.tasks
    if compression != 1.0:
        tasks = {key: replace(task, release_time_s=task.release_time_s * compression) for key, task in tasks.items()}
    return Workload(workload_id=workload.workload_id, tasks=tasks, edges=workload.edges, machines=machines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "experiment_config.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--schedulers", nargs="+", default=["HEFT", "EADAGS_adapted", "Proposed", "ProposedV2"])
    parser.add_argument("--hetero", action="store_true")
    parser.add_argument("--machines", type=int, default=None)
    parser.add_argument("--arrival-compression", type=float, default=1.0)
    parser.add_argument("--target-tasks", type=int, default=100)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=None)
    parser.add_argument("--set", nargs="*", default=[], help="Override scheduler config, e.g. v2_slack_fraction=0.1")
    parser.add_argument("--cfg", nargs="*", default=[],
                        help="Override any config value, e.g. dpm.wake_up_delay_s=30 power.sleep_power_w=22")
    args = parser.parse_args()

    config = load_json(args.config)
    for item in args.set:
        key, value = item.split("=", 1)
        config["scheduler"][key] = float(value)
    for item in args.cfg:
        dotted, value = item.split("=", 1)
        section, key = dotted.split(".", 1)
        config[section][key] = float(value)

    real = load_real_subset(args.data_dir, config)
    catalog = list(build_catalog(real, config, args.target_tasks))
    end = len(catalog) if args.end is None else min(args.end, len(catalog))

    machines = dict(real.machines)
    if args.machines is not None:
        rng = np.random.default_rng(2026)
        chosen = sorted(rng.choice(sorted(machines), size=args.machines, replace=False))
        machines = {mid: machines[mid] for mid in chosen}
    if args.hetero:
        machines = apply_heterogeneity(machines)

    out = args.output_dir
    (out / "runs").mkdir(parents=True, exist_ok=True)
    (out / "settings.json").write_text(
        json.dumps(
            {
                "hetero": args.hetero,
                "machines": len(machines),
                "arrival_compression": args.arrival_compression,
                "target_tasks": args.target_tasks,
                "schedulers": args.schedulers,
                "scheduler_config": config["scheduler"],
                "config_overrides": args.cfg,
                "machine_classes": MACHINE_CLASSES if args.hetero else None,
            },
            indent=2,
        )
    )

    for index in range(args.start, end):
        workload, family, dag_size, arrival, communication, seed = catalog[index]
        run_path = out / "runs" / f"{index:03d}_{workload.workload_id}.csv"
        if run_path.exists():
            continue
        workload = transform(workload, machines, args.arrival_compression)
        records = []
        for scheduler_name in args.schedulers:
            scheduler = get_scheduler(scheduler_name, config)
            started = time.perf_counter()
            schedule = scheduler.schedule(workload)
            runtime = time.perf_counter() - started
            for dpm_enabled in (False, True):
                replay = replay_with_common_dpm(workload, schedule, config, dpm_enabled=dpm_enabled)
                record = dict(replay.metrics)
                record.update(
                    {
                        "index": index,
                        "variant": f"{scheduler.name}_{'DPM' if dpm_enabled else 'NoDPM'}",
                        "family": family,
                        "dag_size": dag_size,
                        "arrival_condition": arrival,
                        "communication_condition": communication,
                        "seed": seed,
                        "scheduling_runtime_s": runtime,
                        "candidate_evaluations": scheduler.candidate_evaluations,
                    }
                )
                if args.hetero:
                    summary = replay.machine_summary.copy()
                    summary["machine_class"] = summary["machine_id"].map(
                        {mid: m.machine_class for mid, m in workload.machines.items()}
                    )
                    by_class = summary.groupby("machine_class")["active_time_s"].sum()
                    for name in MACHINE_CLASSES:
                        record[f"active_time_{name}_s"] = float(by_class.get(name, 0.0))
                records.append(record)
        pd.DataFrame(records).to_csv(run_path, index=False)
        print(f"[{index}] {workload.workload_id} done", flush=True)


if __name__ == "__main__":
    main()
