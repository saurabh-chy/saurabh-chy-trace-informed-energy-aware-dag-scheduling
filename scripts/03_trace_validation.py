from __future__ import annotations

import argparse
from pathlib import Path
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
from scipy.spatial.distance import jensenshannon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reviewer_pipeline.data import (
    bootstrap_synthetic_workload,
    load_json,
    load_real_subset,
    workload_job_metrics,
)


def js_divergence(real: np.ndarray, synthetic: np.ndarray, bins: int = 30) -> float:
    combined = np.concatenate([real, synthetic])
    if np.allclose(combined.min(), combined.max()):
        return 0.0
    edges = np.histogram_bin_edges(combined, bins=bins)
    real_hist, _ = np.histogram(real, bins=edges, density=False)
    synthetic_hist, _ = np.histogram(synthetic, bins=edges, density=False)
    real_prob = (real_hist + 1e-12) / (real_hist.sum() + 1e-12 * len(real_hist))
    synthetic_prob = (synthetic_hist + 1e-12) / (synthetic_hist.sum() + 1e-12 * len(synthetic_hist))
    return float(jensenshannon(real_prob, synthetic_prob, base=2.0) ** 2)


def empirical_cdf(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    values = np.sort(values)
    y = np.arange(1, len(values) + 1) / len(values)
    return values, y


def main() -> None:
    parser = argparse.ArgumentParser(description="Quantitatively validate trace-calibrated synthetic workloads against the real subset.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "experiment_config.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "trace_validation")
    parser.add_argument("--synthetic-job-count", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    figure_dir = args.output_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)

    config = load_json(args.config)
    real_workload = load_real_subset(args.data_dir, config)
    synthetic_workload = bootstrap_synthetic_workload(
        real_workload,
        number_of_jobs=args.synthetic_job_count,
        seed=args.seed,
        arrival_scale=1.0,
        workload_id=f"synthetic_validation_s{args.seed}",
    )

    real_metrics = workload_job_metrics(real_workload)
    synthetic_metrics = workload_job_metrics(synthetic_workload)
    real_metrics.to_csv(args.output_dir / "real_job_metrics.csv", index=False)
    synthetic_metrics.to_csv(args.output_dir / "synthetic_job_metrics.csv", index=False)

    variables = [
        "task_count",
        "edge_count",
        "dag_depth",
        "dag_width",
        "edge_density",
        "total_reference_runtime_s",
        "median_task_runtime_s",
        "total_instances",
        "mean_cpu_fraction",
        "mean_memory_fraction",
        "interarrival_time_s",
    ]

    summary_rows = []
    for variable in variables:
        real_values = real_metrics[variable].replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=float)
        synthetic_values = synthetic_metrics[variable].replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=float)
        ks = stats.ks_2samp(real_values, synthetic_values, alternative="two-sided", method="auto")
        wasserstein = stats.wasserstein_distance(real_values, synthetic_values)
        real_median = float(np.median(real_values))
        synthetic_median = float(np.median(synthetic_values))
        relative_median_difference = (
            (synthetic_median - real_median) / abs(real_median) * 100.0
            if real_median != 0
            else np.nan
        )
        summary_rows.append(
            {
                "variable": variable,
                "real_n": len(real_values),
                "synthetic_n": len(synthetic_values),
                "real_mean": float(np.mean(real_values)),
                "synthetic_mean": float(np.mean(synthetic_values)),
                "real_median": real_median,
                "synthetic_median": synthetic_median,
                "real_iqr": float(np.quantile(real_values, 0.75) - np.quantile(real_values, 0.25)),
                "synthetic_iqr": float(np.quantile(synthetic_values, 0.75) - np.quantile(synthetic_values, 0.25)),
                "relative_median_difference_percent": relative_median_difference,
                "ks_statistic": float(ks.statistic),
                "ks_p_value": float(ks.pvalue),
                "wasserstein_distance": float(wasserstein),
                "jensen_shannon_divergence": js_divergence(real_values, synthetic_values),
            }
        )

        x_real, y_real = empirical_cdf(real_values)
        x_synthetic, y_synthetic = empirical_cdf(synthetic_values)
        fig, ax = plt.subplots(figsize=(7.2, 4.8))
        ax.step(x_real, y_real, where="post", label="Real subset")
        ax.step(x_synthetic, y_synthetic, where="post", label="Trace-calibrated synthetic")
        if variable in {"total_reference_runtime_s", "median_task_runtime_s", "interarrival_time_s", "total_instances"}:
            positive = np.concatenate([real_values[real_values > 0], synthetic_values[synthetic_values > 0]])
            if len(positive) and positive.max() / max(positive.min(), 1e-12) > 100:
                ax.set_xscale("log")
        ax.set_xlabel(variable.replace("_", " "))
        ax.set_ylabel("Empirical cumulative probability")
        ax.set_title(f"Real versus synthetic distribution: {variable.replace('_', ' ')}")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(figure_dir / f"ecdf_{variable}.png", dpi=300)
        fig.savefig(figure_dir / f"ecdf_{variable}.pdf")
        plt.close(fig)

    pd.DataFrame(summary_rows).to_csv(args.output_dir / "trace_validation_statistics.csv", index=False)

    # Compact reviewer-facing boxplot panel as separate figures, not subplots.
    for variable in ["task_count", "dag_depth", "dag_width", "median_task_runtime_s", "interarrival_time_s"]:
        fig, ax = plt.subplots(figsize=(6.2, 4.6))
        ax.boxplot(
            [real_metrics[variable].dropna(), synthetic_metrics[variable].dropna()],
            tick_labels=["Real subset", "Synthetic"],
            showfliers=False,
        )
        if variable in {"median_task_runtime_s", "interarrival_time_s"}:
            ax.set_yscale("symlog", linthresh=1.0)
        ax.set_ylabel(variable.replace("_", " "))
        ax.set_title(f"Workload validation: {variable.replace('_', ' ')}")
        ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(figure_dir / f"boxplot_{variable}.png", dpi=300)
        fig.savefig(figure_dir / f"boxplot_{variable}.pdf")
        plt.close(fig)

    print(f"Trace-validation outputs written to {args.output_dir}")


if __name__ == "__main__":
    main()
