from __future__ import annotations

import argparse
from pathlib import Path
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reviewer_pipeline.data import load_json
from reviewer_pipeline.statistics import common_dpm_analysis


def grouped_metric_figure(results: pd.DataFrame, metric: str, ylabel: str, output: Path) -> None:
    summary = (
        results.groupby(["scheduler", "dpm_enabled"])[metric]
        .agg(["mean", "std", "count"])
        .reset_index()
    )
    summary["se"] = summary["std"] / np.sqrt(summary["count"].clip(lower=1))
    summary["ci95"] = 1.96 * summary["se"]
    schedulers = sorted(summary["scheduler"].unique())
    x = np.arange(len(schedulers))
    width = 0.36

    fig, ax = plt.subplots(figsize=(8.2, 5.0))
    for offset, enabled in [(-width / 2, False), (width / 2, True)]:
        part = summary[summary["dpm_enabled"] == enabled].set_index("scheduler").reindex(schedulers)
        ax.bar(
            x + offset,
            part["mean"],
            width,
            yerr=part["ci95"],
            capsize=4,
            label="DPM enabled" if enabled else "DPM disabled",
        )
    ax.set_xticks(x, schedulers)
    ax.set_ylabel(ylabel)
    ax.set_title(f"Common-DPM comparison: {ylabel}")
    ax.legend()
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(output.with_suffix(".png"), dpi=300)
    fig.savefig(output.with_suffix(".pdf"))
    plt.close(fig)


def paired_improvement_figure(common_dpm: pd.DataFrame, metric: str, output: Path) -> None:
    frame = common_dpm[common_dpm["metric"] == metric].copy()
    if frame.empty:
        return
    frame = frame.sort_values("mean_improvement_percent")
    labels = frame["comparator"].str.replace("_DPM", "", regex=False)
    values = frame["mean_improvement_percent"].to_numpy()
    lower = values - frame["ci_low_percent"].to_numpy()
    upper = frame["ci_high_percent"].to_numpy() - values

    fig, ax = plt.subplots(figsize=(7.2, 4.8))
    ax.errorbar(values, np.arange(len(values)), xerr=np.vstack([lower, upper]), fmt="o", capsize=4)
    ax.axvline(0.0, linewidth=1)
    ax.set_yticks(np.arange(len(values)), labels)
    ax.set_xlabel("Mean paired improvement of Proposed-DPM (%)")
    ax.set_title(f"Proposed versus baselines under the same DPM controller: {metric}")
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(output.with_suffix(".png"), dpi=300)
    fig.savefig(output.with_suffix(".pdf"))
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyse common-DPM results with paired tests, bootstrap CIs, and figures.")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "experiment_config.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "common_dpm_analysis")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    figure_dir = args.output_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)

    config = load_json(args.config)
    results = pd.read_csv(args.results)
    analyses = common_dpm_analysis(results, config)
    for name, frame in analyses.items():
        frame.to_csv(args.output_dir / f"{name}_analysis.csv", index=False)

    grouped_metric_figure(results, "total_energy_j", "Total energy (J)", figure_dir / "common_dpm_total_energy")
    grouped_metric_figure(results, "edp_j_s", "Energy-delay product (J·s)", figure_dir / "common_dpm_edp")
    grouped_metric_figure(results, "makespan_s", "Makespan (s)", figure_dir / "common_dpm_makespan")

    common = analyses.get("common_dpm", pd.DataFrame())
    for metric in ["total_energy_j", "edp_j_s", "makespan_s"]:
        paired_improvement_figure(common, metric, figure_dir / f"paired_{metric}")

    overall = (
        results.groupby(["scheduler", "dpm_enabled"])
        .agg(
            workloads=("workload_id", "nunique"),
            makespan_mean_s=("makespan_s", "mean"),
            energy_mean_j=("total_energy_j", "mean"),
            edp_mean_j_s=("edp_j_s", "mean"),
            sleep_time_mean_s=("total_sleep_time_s", "mean"),
            sleep_entries_mean=("sleep_entries", "mean"),
            scheduler_runtime_mean_s=("scheduling_runtime_s", "mean"),
        )
        .reset_index()
    )
    overall.to_csv(args.output_dir / "common_dpm_overall_summary.csv", index=False)
    print(f"Analysis written to {args.output_dir}")


if __name__ == "__main__":
    main()
