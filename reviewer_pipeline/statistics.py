from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import stats


@dataclass(frozen=True)
class PairedComparison:
    comparator: str
    proposed: str
    metric: str
    n: int
    comparator_mean: float
    proposed_mean: float
    mean_improvement_percent: float
    ci_low_percent: float
    ci_high_percent: float
    test: str
    statistic: float
    p_value: float
    adjusted_p_value: float
    effect_name: str
    effect_size: float


def paired_rank_biserial(differences: np.ndarray) -> float:
    differences = np.asarray(differences, dtype=float)
    nonzero = differences[differences != 0]
    if len(nonzero) == 0:
        return 0.0
    ranks = stats.rankdata(np.abs(nonzero))
    positive = ranks[nonzero > 0].sum()
    negative = ranks[nonzero < 0].sum()
    denominator = positive + negative
    return float((positive - negative) / denominator) if denominator else 0.0


def cohen_dz(differences: np.ndarray) -> float:
    differences = np.asarray(differences, dtype=float)
    standard_deviation = differences.std(ddof=1)
    if standard_deviation == 0:
        return float(np.sign(differences.mean()) * np.inf) if differences.mean() else 0.0
    return float(differences.mean() / standard_deviation)


def bootstrap_mean_ci(
    values: np.ndarray,
    resamples: int = 2000,
    alpha: float = 0.05,
    seed: int = 42,
) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    samples = rng.choice(values, size=(resamples, len(values)), replace=True)
    means = samples.mean(axis=1)
    return (
        float(np.quantile(means, alpha / 2)),
        float(np.quantile(means, 1 - alpha / 2)),
    )


def holm_adjust(p_values: Sequence[float]) -> np.ndarray:
    p = np.asarray(p_values, dtype=float)
    order = np.argsort(p)
    adjusted = np.empty_like(p)
    running = 0.0
    m = len(p)
    for rank, index in enumerate(order):
        candidate = (m - rank) * p[index]
        running = max(running, candidate)
        adjusted[index] = min(running, 1.0)
    return adjusted


def compare_paired(
    results: pd.DataFrame,
    proposed_name: str,
    comparator_names: Iterable[str],
    metric: str,
    id_column: str = "workload_id",
    scheduler_column: str = "variant",
    bootstrap_resamples: int = 2000,
    alpha: float = 0.05,
    improvement_direction: str = "lower_is_better",
) -> pd.DataFrame:
    rows = []
    raw_p_values = []
    interim = []

    for comparator in comparator_names:
        subset = results[results[scheduler_column].isin([proposed_name, comparator])]
        pivot = subset.pivot_table(index=id_column, columns=scheduler_column, values=metric, aggfunc="first").dropna()
        if proposed_name not in pivot or comparator not in pivot:
            continue
        comparator_values = pivot[comparator].to_numpy(dtype=float)
        proposed_values = pivot[proposed_name].to_numpy(dtype=float)
        if improvement_direction == "lower_is_better":
            differences = comparator_values - proposed_values
            improvements = (comparator_values - proposed_values) / np.where(comparator_values == 0, np.nan, comparator_values) * 100
        else:
            differences = proposed_values - comparator_values
            improvements = (proposed_values - comparator_values) / np.where(comparator_values == 0, np.nan, comparator_values) * 100
        improvements = improvements[np.isfinite(improvements)]
        ci_low, ci_high = bootstrap_mean_ci(improvements, bootstrap_resamples, alpha)

        if len(differences) >= 3:
            try:
                shapiro_p = stats.shapiro(differences).pvalue
            except ValueError:
                shapiro_p = 0.0
        else:
            shapiro_p = 0.0

        if shapiro_p >= alpha:
            test_result = stats.ttest_rel(comparator_values, proposed_values, nan_policy="omit")
            test_name = "paired_t"
            statistic = float(test_result.statistic)
            p_value = float(test_result.pvalue)
            effect_name = "cohen_dz"
            effect = cohen_dz(differences)
        else:
            try:
                test_result = stats.wilcoxon(differences, alternative="two-sided", zero_method="wilcox")
                statistic = float(test_result.statistic)
                p_value = float(test_result.pvalue)
            except ValueError:
                statistic = 0.0
                p_value = 1.0
            test_name = "wilcoxon_signed_rank"
            effect_name = "rank_biserial"
            effect = paired_rank_biserial(differences)

        interim.append(
            {
                "comparator": comparator,
                "proposed": proposed_name,
                "metric": metric,
                "n": len(pivot),
                "comparator_mean": float(np.mean(comparator_values)),
                "proposed_mean": float(np.mean(proposed_values)),
                "mean_improvement_percent": float(np.nanmean(improvements)),
                "ci_low_percent": ci_low,
                "ci_high_percent": ci_high,
                "normality_p": float(shapiro_p),
                "test": test_name,
                "statistic": statistic,
                "p_value": p_value,
                "effect_name": effect_name,
                "effect_size": effect,
            }
        )
        raw_p_values.append(p_value)

    adjusted = holm_adjust(raw_p_values) if raw_p_values else np.array([])
    for record, adjusted_p in zip(interim, adjusted):
        record["holm_adjusted_p"] = float(adjusted_p)
        rows.append(record)
    return pd.DataFrame(rows)


def common_dpm_analysis(results: pd.DataFrame, config: Mapping) -> dict[str, pd.DataFrame]:
    """Produce the three reviewer-facing analyses for the DPM fairness experiment."""
    output: dict[str, pd.DataFrame] = {}
    metrics = ["makespan_s", "total_energy_j", "edp_j_s"]
    resamples = int(config["statistics"].get("bootstrap_resamples", 2000))
    alpha = float(config["statistics"].get("alpha", 0.05))

    # Within-scheduler DPM effect.
    within_rows = []
    for scheduler in sorted(results["scheduler"].unique()):
        subset = results[results["scheduler"] == scheduler].copy()
        subset["variant"] = np.where(subset["dpm_enabled"], f"{scheduler}_DPM", f"{scheduler}_NoDPM")
        for metric in metrics:
            comparison = compare_paired(
                subset,
                proposed_name=f"{scheduler}_DPM",
                comparator_names=[f"{scheduler}_NoDPM"],
                metric=metric,
                scheduler_column="variant",
                bootstrap_resamples=resamples,
                alpha=alpha,
            )
            if not comparison.empty:
                comparison.insert(0, "analysis", "within_scheduler_dpm_effect")
                within_rows.append(comparison)
    output["within_scheduler"] = pd.concat(within_rows, ignore_index=True) if within_rows else pd.DataFrame()

    # Proposed versus baselines under common DPM.
    dpm_on = results[results["dpm_enabled"]].copy()
    dpm_on["variant"] = dpm_on["scheduler"] + "_DPM"
    common_rows = []
    comparators = [name for name in sorted(dpm_on["variant"].unique()) if name != "Proposed_DPM"]
    for metric in metrics:
        comparison = compare_paired(
            dpm_on,
            proposed_name="Proposed_DPM",
            comparator_names=comparators,
            metric=metric,
            scheduler_column="variant",
            bootstrap_resamples=resamples,
            alpha=alpha,
        )
        if not comparison.empty:
            comparison.insert(0, "analysis", "common_dpm_scheduler_comparison")
            common_rows.append(comparison)
    output["common_dpm"] = pd.concat(common_rows, ignore_index=True) if common_rows else pd.DataFrame()

    # Difference-in-differences interaction.
    interaction_rows = []
    proposed = results[results["scheduler"] == "Proposed"].pivot_table(
        index="workload_id", columns="dpm_enabled", values=metrics, aggfunc="first"
    )
    for baseline in [name for name in sorted(results["scheduler"].unique()) if name != "Proposed"]:
        baseline_frame = results[results["scheduler"] == baseline].pivot_table(
            index="workload_id", columns="dpm_enabled", values=metrics, aggfunc="first"
        )
        for metric in metrics:
            try:
                proposed_delta = proposed[metric][True] - proposed[metric][False]
                baseline_delta = baseline_frame[metric][True] - baseline_frame[metric][False]
            except KeyError:
                continue
            joined = pd.concat([proposed_delta.rename("proposed"), baseline_delta.rename("baseline")], axis=1).dropna()
            interaction = joined["proposed"] - joined["baseline"]
            ci_low, ci_high = bootstrap_mean_ci(interaction.to_numpy(), resamples, alpha)
            interaction_rows.append(
                {
                    "baseline": baseline,
                    "metric": metric,
                    "n": len(joined),
                    "mean_difference_in_differences": float(interaction.mean()),
                    "ci_low": ci_low,
                    "ci_high": ci_high,
                }
            )
    output["interaction"] = pd.DataFrame(interaction_rows)
    return output
