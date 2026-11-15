from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def bootstrap_ci(values: np.ndarray, resamples: int = 2000, seed: int = 42) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    draws = rng.choice(values, size=(resamples, len(values)), replace=True).mean(axis=1)
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def plot_metric(frame: pd.DataFrame, metric: str, ylabel: str, output: Path) -> None:
    rows = []
    for scheduler, group in frame.groupby("scheduler"):
        values = group[metric].dropna().to_numpy(dtype=float)
        low, high = bootstrap_ci(values)
        mean = float(values.mean())
        rows.append((scheduler, mean, low, high))
    summary = pd.DataFrame(rows, columns=["scheduler", "mean", "low", "high"]).sort_values("mean")
    x = np.arange(len(summary))
    lower = summary["mean"] - summary["low"]
    upper = summary["high"] - summary["mean"]

    fig, ax = plt.subplots(figsize=(8.2, 4.9))
    ax.bar(x, summary["mean"], yerr=np.vstack([lower, upper]), capsize=4)
    ax.set_xticks(x, summary["scheduler"], rotation=25, ha="right")
    ax.set_ylabel(ylabel)
    ax.set_title(f"Mean {ylabel.lower()} with 95% bootstrap confidence intervals")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(output.with_suffix(".png"), dpi=300)
    fig.savefig(output.with_suffix(".pdf"))
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate main-result figures with reviewer-requested uncertainty bars.")
    parser.add_argument("--results", type=Path, required=True, help="One row per workload-scheduler run.")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    frame = pd.read_csv(args.results)
    required = {"scheduler", "makespan_s", "total_energy_j", "edp_j_s"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Results file is missing columns: {sorted(missing)}")

    plot_metric(frame, "makespan_s", "Makespan (s)", args.output_dir / "makespan_with_ci")
    plot_metric(frame, "total_energy_j", "Total energy (J)", args.output_dir / "energy_with_ci")
    plot_metric(frame, "edp_j_s", "Energy-delay product (J·s)", args.output_dir / "edp_with_ci")


if __name__ == "__main__":
    main()
