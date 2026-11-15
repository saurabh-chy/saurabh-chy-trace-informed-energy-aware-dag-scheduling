from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def make_line_plot(frame: pd.DataFrame, y: str, ylabel: str, output: Path) -> None:
    summary = frame.groupby(["scheduler", "machine_count", "realised_task_count"])[y].mean().reset_index()
    for machine_count in sorted(summary["machine_count"].unique()):
        subset = summary[summary["machine_count"] == machine_count]
        fig, ax = plt.subplots(figsize=(7.2, 4.8))
        for scheduler in sorted(subset["scheduler"].unique()):
            line = subset[subset["scheduler"] == scheduler].sort_values("realised_task_count")
            ax.plot(line["realised_task_count"], line[y], marker="o", label=scheduler)
        ax.set_xlabel("Realised task count")
        ax.set_ylabel(ylabel)
        ax.set_title(f"Scalability with {machine_count} machines")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(output.parent / f"{output.stem}_m{machine_count}.png", dpi=300)
        fig.savefig(output.parent / f"{output.stem}_m{machine_count}.pdf")
        plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate reviewer-facing scalability figures.")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    frame = pd.read_csv(args.results)
    make_line_plot(frame, "scheduler_runtime_s", "Scheduler runtime (s)", args.output_dir / "runtime_scalability")
    make_line_plot(frame, "peak_scheduler_memory_bytes", "Peak scheduler memory (bytes)", args.output_dir / "memory_scalability")
    make_line_plot(frame, "edp_j_s", "Energy-delay product (J·s)", args.output_dir / "edp_scalability")
    make_line_plot(frame, "candidate_evaluations", "Candidate evaluations", args.output_dir / "candidate_scalability")


if __name__ == "__main__":
    main()
