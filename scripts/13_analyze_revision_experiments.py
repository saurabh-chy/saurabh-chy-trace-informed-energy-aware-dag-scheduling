"""Analyse the round-2 revision experiments (script 12 outputs).

For each setting, compares Proposed-v2 (DPM) with every other DPM variant on the
same workloads: paired per-workload percentage difference, bootstrap 95% CI,
Wilcoxon signed-rank test, Holm adjustment across the comparisons of a setting.
Positive values mean Proposed-v2 is better (lower).
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reviewer_pipeline.statistics import bootstrap_mean_ci, holm_adjust

METRICS = ["makespan_s", "total_energy_j", "edp_j_s", "computation_energy_j", "idle_sleep_energy_j"]


def load(directory: Path) -> pd.DataFrame:
    files = sorted((directory / "runs").glob("*.csv"))
    if not files:
        return pd.DataFrame()
    return pd.concat((pd.read_csv(f) for f in files), ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    settings = {
        "S0_base": ["S0_base", "S0_base_extra"],
        "S1_hetero": ["S1_hetero", "S1_hetero_oldProposed", "S1_hetero_extra"],
        "S2_util": ["S2_util", "S2_util_noSR", "S2_util_extra"],
        "S3_hetero_util": ["S3_hetero_util", "S3_hetero_util_noSR", "S3_hetero_util_extra"],
    }
    means_rows, paired_rows = [], []
    for setting, parts in settings.items():
        frames = []
        for part in parts:
            frame = load(args.root / part)
            if frame.empty:
                continue
            if part.endswith("noSR"):
                frame["variant"] = frame["variant"].str.replace("ProposedV2", "ProposedV2-noSR", regex=False)
            frames.append(frame)
        if not frames:
            continue
        data = pd.concat(frames, ignore_index=True)
        data = data[data["variant"].str.endswith("_DPM")].copy()
        data["variant"] = data["variant"].str.replace("_DPM", "-DPM", regex=False)
        data.to_csv(args.output_dir / f"{setting}_dpm_runs.csv", index=False)

        summary = data.groupby("variant").agg(
            n=("workload_id", "nunique"),
            makespan_s=("makespan_s", "mean"),
            total_energy_mj=("total_energy_j", lambda s: s.mean() / 1e6),
            edp_1e9_js=("edp_j_s", lambda s: s.mean() / 1e9),
            computation_energy_mj=("computation_energy_j", lambda s: s.mean() / 1e6),
            state_energy_mj=("idle_sleep_energy_j", lambda s: s.mean() / 1e6),
            utilisation_pct=("utilisation_fraction", lambda s: s.mean() * 100),
            scheduling_runtime_s=("scheduling_runtime_s", "mean"),
            candidate_evaluations=("candidate_evaluations", "mean"),
        ).reset_index()
        summary.insert(0, "setting", setting)
        means_rows.append(summary)

        proposed = "ProposedV2-DPM"
        wide = {m: data.pivot_table(index="workload_id", columns="variant", values=m) for m in METRICS}
        comparators = [v for v in data["variant"].unique() if v != proposed]
        setting_rows = []
        for comparator in sorted(comparators):
            for metric in METRICS:
                table = wide[metric][[comparator, proposed]].dropna()
                if table.empty:
                    continue
                pct = (table[comparator] - table[proposed]) / table[comparator] * 100.0
                low, high = bootstrap_mean_ci(pct.to_numpy())
                diff = (table[comparator] - table[proposed]).to_numpy()
                if np.allclose(diff, 0):
                    p_value = 1.0
                else:
                    p_value = float(stats.wilcoxon(diff, zero_method="wilcox").pvalue)
                setting_rows.append(
                    {
                        "setting": setting,
                        "comparator": comparator,
                        "metric": metric,
                        "n": len(table),
                        "mean_improvement_pct": float(pct.mean()),
                        "ci_low_pct": low,
                        "ci_high_pct": high,
                        "wins": int((diff > 1e-9 * np.abs(table[comparator].to_numpy())).sum()),
                        "ties": int((np.abs(diff) <= 1e-9 * np.abs(table[comparator].to_numpy())).sum()),
                        "p_value": p_value,
                    }
                )
        if setting_rows:
            adjusted = holm_adjust([row["p_value"] for row in setting_rows])
            for row, adj in zip(setting_rows, adjusted):
                row["holm_p"] = float(adj)
            paired_rows.extend(setting_rows)

    means = pd.concat(means_rows, ignore_index=True)
    paired = pd.DataFrame(paired_rows)
    means.to_csv(args.output_dir / "means_by_setting.csv", index=False)
    paired.to_csv(args.output_dir / "paired_v2_vs_comparators.csv", index=False)
    pd.set_option("display.width", 250)
    print(means.round(4).to_string(index=False))
    print()
    view = paired[paired["metric"].isin(["makespan_s", "total_energy_j", "edp_j_s"])]
    print(view.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
