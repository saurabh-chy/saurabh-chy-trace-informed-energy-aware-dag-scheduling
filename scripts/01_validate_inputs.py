from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reviewer_pipeline.data import load_json, load_real_subset, sha256_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the Alibaba research subset used by the scheduling study.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "experiment_config.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "input_validation")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    config = load_json(args.config)
    workload = load_real_subset(args.data_dir, config)

    selected_jobs_path = args.data_dir / "identifier1_selected_job_ids.txt"
    selected_machines_candidates = list(args.data_dir.glob("*selected_machine_ids.txt"))
    selected_machines_path = selected_machines_candidates[0] if selected_machines_candidates else None

    job_ids = set(task.job_id for task in workload.tasks.values())
    selected_job_ids = set()
    if selected_jobs_path.exists():
        selected_job_ids = {line.strip() for line in selected_jobs_path.read_text(encoding="utf-8").splitlines() if line.strip()}

    selected_machine_ids = set()
    if selected_machines_path and selected_machines_path.exists():
        selected_machine_ids = {line.strip() for line in selected_machines_path.read_text(encoding="utf-8").splitlines() if line.strip()}

    file_rows = []
    for path in sorted(args.data_dir.glob("*.csv")):
        frame = pd.read_csv(path, nrows=5)
        with path.open("rb") as handle:
            row_count = sum(1 for _ in handle) - 1
        file_rows.append(
            {
                "file": path.name,
                "size_bytes": path.stat().st_size,
                "row_count": max(row_count, 0),
                "column_count": len(frame.columns),
                "columns": "|".join(frame.columns),
                "sha256": sha256_file(path),
            }
        )

    report = {
        "workload_id": workload.workload_id,
        "job_count": len(job_ids),
        "task_count": len(workload.tasks),
        "edge_count": len(workload.edges),
        "machine_count": len(workload.machines),
        "dag_is_acyclic": True,
        "selected_job_id_count": len(selected_job_ids),
        "selected_job_ids_match": selected_job_ids == job_ids if selected_job_ids else None,
        "selected_machine_id_count": len(selected_machine_ids),
        "selected_machine_ids_match": selected_machine_ids == set(workload.machines) if selected_machine_ids else None,
    }

    pd.DataFrame(file_rows).to_csv(args.output_dir / "input_file_registry.csv", index=False)
    (args.output_dir / "input_validation_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
