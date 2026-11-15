from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def run(command: list[str]) -> None:
    print("\n$", " ".join(command))
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the reviewer-compliance analysis pipeline.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs" / "full_run")
    parser.add_argument("--target-tasks", type=int, default=100)
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)

    python = sys.executable
    config = ROOT / "configs" / "experiment_config.json"

    run([python, str(ROOT / "scripts" / "01_validate_inputs.py"), "--data-dir", str(args.data_dir), "--config", str(config), "--output-dir", str(args.output_root / "input_validation")])
    run([python, str(ROOT / "scripts" / "03_trace_validation.py"), "--data-dir", str(args.data_dir), "--config", str(config), "--output-dir", str(args.output_root / "trace_validation")])
    run([python, str(ROOT / "scripts" / "02_run_common_dpm_baselines.py"), "--data-dir", str(args.data_dir), "--config", str(config), "--target-tasks", str(args.target_tasks), "--output-dir", str(args.output_root / "common_dpm")])
    run([python, str(ROOT / "scripts" / "05_analyze_common_dpm.py"), "--results", str(args.output_root / "common_dpm" / "common_dpm_results.csv"), "--config", str(config), "--output-dir", str(args.output_root / "common_dpm_analysis")])
    run([python, str(ROOT / "scripts" / "04_run_scalability.py"), "--data-dir", str(args.data_dir), "--config", str(config), "--output-dir", str(args.output_root / "scalability")])
    run([python, str(ROOT / "scripts" / "06_generate_scalability_figures.py"), "--results", str(args.output_root / "scalability" / "scalability_results.csv"), "--output-dir", str(args.output_root / "scalability" / "figures")])
    run([python, str(ROOT / "scripts" / "07_generate_workflow_figure.py"), "--output-dir", str(args.output_root / "workflow_figure")])
    run([python, str(ROOT / "scripts" / "08_generate_methodology_tables.py"), "--config", str(config), "--output-dir", str(args.output_root / "methodology_tables")])
    run([python, str(ROOT / "scripts" / "09_environment_report.py"), "--output-dir", str(args.output_root / "environment")])


if __name__ == "__main__":
    main()
