"""Generate every number, table, and figure used in the round-2 manuscript.

Inputs: raw run folders from scripts 12/04 (OUT_DIR, default outputs/revision_round2/raw),
the published common-DPM results (outputs/common_dpm_full), and trace-validation outputs (script 03).
Outputs: <out>/nums.json, <out>/tables/*.csv, <out>/figures/*.png
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reviewer_pipeline.statistics import bootstrap_mean_ci, holm_adjust  # noqa: E402

# Fixed categorical order (validated palette): colour follows the scheduler, never its rank.
COLORS = {
    "HEFT": "#2a78d6",
    "EADAGS": "#eb6834",
    "EADAGS-adapted": "#1baf7a",
    "Proposed (v1)": "#eda100",
    "Proposed-v2": "#e87ba4",
}
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
LABEL = {
    "HEFT": "HEFT",
    "EADAGS_SR": "EADAGS",
    "EADAGS_adapted": "EADAGS-adapted",
    "Proposed": "Proposed (v1)",
    "ProposedV2": "Proposed-v2",
    "HEFT_DPMSR": "HEFT + DPM-aware SR",
    "ProposedV2-noSR": "Proposed-v2 without SR",
}
ORDER = ["HEFT", "EADAGS", "EADAGS-adapted", "Proposed (v1)", "Proposed-v2"]

plt.rcParams.update({
    "font.family": "DejaVu Serif", "font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK,
    "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "legend.frameon": False, "savefig.dpi": 300,
})


def load_runs(folder: Path) -> pd.DataFrame:
    files = sorted(glob.glob(str(folder / "runs" / "*.csv")))
    return pd.concat((pd.read_csv(f) for f in files), ignore_index=True) if files else pd.DataFrame()


def tidy(frame: pd.DataFrame, suffix: str = "") -> pd.DataFrame:
    frame = frame.copy()
    frame["dpm"] = frame["variant"].str.endswith("_DPM")
    base = frame["variant"].str.replace(r"_(No)?DPM$", "", regex=True)
    frame["scheduler"] = (base + suffix).map(lambda s: LABEL.get(s, s))
    return frame


def setting_frame(raw: Path, parts) -> pd.DataFrame:
    frames = []
    for part, suffix in parts:
        f = load_runs(raw / part)
        if not f.empty:
            frames.append(tidy(f, suffix))
    return pd.concat(frames, ignore_index=True)


def paired(df, a, b, metric):
    """Mean % improvement of b over a (positive = b lower), CI, wins, Wilcoxon p."""
    w = df.pivot_table(index="workload_id", columns="scheduler", values=metric)[[a, b]].dropna()
    pct = (w[a] - w[b]) / w[a] * 100
    lo, hi = bootstrap_mean_ci(pct.to_numpy())
    diff = (w[a] - w[b]).to_numpy()
    p = 1.0 if np.allclose(diff, 0) else float(stats.wilcoxon(diff).pvalue)
    tol = 1e-9 * np.abs(w[a].to_numpy())
    return {"mean": float(pct.mean()), "lo": float(lo), "hi": float(hi), "n": len(w),
            "wins": int((diff > tol).sum()), "losses": int((diff < -tol).sum()), "p": p}


def fmt_ci(r, d=2):
    return f"{r['mean']:+.{d}f} [{r['lo']:+.{d}f}, {r['hi']:+.{d}f}]"


def fmt_p(p):
    return "< 0.001" if p < 0.001 else f"{p:.3f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=Path(os.environ.get("OUT_DIR", ROOT / "outputs/revision_round2/raw")))
    ap.add_argument("--trace-validation", type=Path, default=ROOT / "outputs/trace_validation")
    ap.add_argument("--out", type=Path, default=ROOT / "outputs/revision_round2/manuscript")
    args = ap.parse_args()
    out, raw = args.out, args.raw
    (out / "tables").mkdir(parents=True, exist_ok=True)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    N = {}

    # ---------------- settings data -------------------------------------------------
    S = {
        "S0": setting_frame(raw, [("S0_base", ""), ("S0_base_extra", "")]),
        "S1": setting_frame(raw, [("S1_hetero", ""), ("S1_hetero_extra", ""), ("S1_hetero_oldProposed", "")]),
        "S2": setting_frame(raw, [("S2_util", ""), ("S2_util_extra", ""), ("S2_util_noSR", "-noSR")]),
        "S3": setting_frame(raw, [("S3_hetero_util", ""), ("S3_hetero_util_extra", ""), ("S3_hetero_util_noSR", "-noSR")]),
    }
    # Original Proposed (v1) in S0 comes from the published common-DPM run (reproduced exactly; see README).
    old = pd.read_csv(ROOT / "outputs/common_dpm_full/common_dpm_results.csv")
    old = old[old["variant"].str.startswith("Proposed")].copy()
    old = tidy(old)
    S["S0"] = pd.concat([S["S0"], old[[c for c in old.columns if c in S["S0"].columns]]], ignore_index=True)
    for key in S:
        S[key]["scheduler"] = S[key]["scheduler"].replace({"ProposedV2-noSR": LABEL["ProposedV2-noSR"]})
    D = {k: v[v["dpm"]] for k, v in S.items()}

    # ---------------- table: means by setting ---------------------------------------
    rows = []
    main_sched = ORDER
    for key, d in D.items():
        g = d.groupby("scheduler")
        for s in main_sched:
            if s not in g.groups:
                continue
            x = g.get_group(s)
            rows.append({"setting": key, "scheduler": s, "n": x["workload_id"].nunique(),
                         "makespan_s": x["makespan_s"].mean(), "energy_mj": x["total_energy_j"].mean() / 1e6,
                         "edp": x["edp_j_s"].mean(), "util_pct": x["utilisation_fraction"].mean() * 100,
                         "comp_mj": x["computation_energy_j"].mean() / 1e6,
                         "state_mj": x["idle_sleep_energy_j"].mean() / 1e6,
                         "runtime_s": x["scheduling_runtime_s"].mean(),
                         "evals": x["candidate_evaluations"].mean()})
    means = pd.DataFrame(rows)
    means.to_csv(out / "tables/means_by_setting.csv", index=False)

    # S0 table with DPM effect (DPM vs NoDPM of the same scheduler)
    s0rows = []
    for s in main_sched:
        a = S["S0"][S["S0"]["scheduler"] == s]
        if a.empty:
            continue
        nd = a[~a["dpm"]].set_index("workload_id")
        dp = a[a["dpm"]].set_index("workload_id")
        red = ((nd["total_energy_j"] - dp["total_energy_j"]) / nd["total_energy_j"] * 100).mean()
        s0rows.append([s, f"{dp['makespan_s'].mean():,.0f}", f"{dp['total_energy_j'].mean() / 1e6:,.1f}",
                       f"{dp['edp_j_s'].mean() / 1e15:,.4f}", f"{nd['total_energy_j'].mean() / 1e6:,.1f}", f"{red:.1f}"])
        if s == "HEFT":
            N["S0_dpm_saving"] = float(red)
    N["S0_rows"] = s0rows
    d0 = D["S0"]
    N["S0_state_share"] = float((d0["idle_sleep_energy_j"] / d0["total_energy_j"]).mean() * 100)
    N["S0_util_pct"] = float(d0[d0.scheduler == "HEFT"]["utilisation_fraction"].mean() * 100)
    # Paired EDP differences in S0 (vs HEFT-DPM)
    s0p = {s: paired(d0, "HEFT", s, "edp_j_s") for s in ["EADAGS", "EADAGS-adapted", "Proposed (v1)", "Proposed-v2"]}
    N["S0_paired"] = {s: r for s, r in s0p.items()}
    N["S0_max_diff_mp"] = max(abs(s0p[s]["mean"]) for s in ["EADAGS", "Proposed-v2"])
    N["S0_max_diff"] = max(abs(r["mean"]) for r in s0p.values())
    N["S0_v1_vs_heft"] = s0p["Proposed (v1)"]["mean"]

    # ---------------- paired v2 comparisons in all settings -------------------------
    prow, raw_rows = [], []
    for key in ["S0", "S1", "S2", "S3"]:
        d = D[key]
        recs = []
        for comp in ["HEFT", "EADAGS", "EADAGS-adapted", "Proposed (v1)"]:
            if comp not in set(d["scheduler"]):
                continue
            r = {m: paired(d, comp, "Proposed-v2", m) for m in ["makespan_s", "total_energy_j", "edp_j_s"]}
            recs.append((comp, r))
        ps = [r[m]["p"] for _, r in recs for m in r]
        adj = holm_adjust(ps)
        i = 0
        for comp, r in recs:
            for m in r:
                r[m]["holm"] = float(adj[i])
                i += 1
            raw_rows.append({"setting": key, "comparator": comp, **{f"{m}_{k}": v for m in r for k, v in r[m].items()}})
            if key in ("S2", "S3"):
                prow.append([key, comp, fmt_ci(r["makespan_s"]), fmt_ci(r["total_energy_j"]), fmt_ci(r["edp_j_s"]),
                             f"{r['edp_j_s']['wins']}/{r['edp_j_s']['n']}", fmt_p(r["edp_j_s"]["holm"])])
            N[f"v2_vs_{comp}_{key}"] = r
    pd.DataFrame(raw_rows).to_csv(out / "tables/paired_v2.csv", index=False)
    N["paired_rows"] = prow
    for key in ["S2", "S3"]:
        w = D[key].pivot_table(index="workload_id", columns="scheduler", values="makespan_s")
        dmk = (w["Proposed-v2"] - w["HEFT"]) / w["HEFT"] * 100
        N[f"mk_{key}"] = {"longer": int((dmk > 1e-7).sum()), "max": float(dmk.max()),
                          "shorter": int((dmk < -1e-7).sum()), "min": float(dmk.min()), "mean": float(dmk.mean())}
    N["v2_heft_S2"] = N["v2_vs_HEFT_S2"]["edp_j_s"]["mean"]
    N["v2_heft_S3"] = N["v2_vs_HEFT_S3"]["edp_j_s"]["mean"]
    N["v2_eads_S23"] = (N["v2_vs_EADAGS_S2"]["edp_j_s"]["mean"] + N["v2_vs_EADAGS_S3"]["edp_j_s"]["mean"]) / 2

    # means rows for S2/S3 table
    mrows = []
    for key in ["S1", "S2", "S3"]:
        for s in main_sched:
            x = means[(means.setting == key) & (means.scheduler == s)]
            if x.empty:
                continue
            x = x.iloc[0]
            mrows.append([key, s, f"{x.makespan_s:,.1f}", f"{x.energy_mj:,.3f}", f"{x.edp / 1e9:,.3f}",
                          (f"{x.util_pct:.1f}" if x.util_pct >= 1 else f"{x.util_pct:.4f}"), f"{x.runtime_s:.3f}"])
    N["means_rows"] = mrows
    N["util_S2"] = float(means[(means.setting == "S2") & (means.scheduler == "HEFT")].util_pct.iloc[0])
    N["util_S3"] = float(means[(means.setting == "S3") & (means.scheduler == "HEFT")].util_pct.iloc[0])
    N["util_S1"] = float(means[(means.setting == "S1") & (means.scheduler == "HEFT")].util_pct.iloc[0])
    N["util_S0"] = N["S0_util_pct"]
    for key in ["S1", "S2", "S3"]:
        N[f"rt_{key}"] = {r.scheduler: float(r.runtime_s) for r in means[means.setting == key].itertuples()}
        N[f"ev_{key}"] = {r.scheduler: float(r.evals) for r in means[means.setting == key].itertuples()}
    N["state_share"] = {k: float((D[k]["idle_sleep_energy_j"] / D[k]["total_energy_j"]).mean() * 100) for k in D}

    # ---------------- ablation -------------------------------------------------------
    abl = []
    for key in ["S2", "S3"]:
        d = D[key]
        for comp, name in [("HEFT", "HEFT (no DPM-aware placement, no SR)"),
                           (LABEL["ProposedV2-noSR"], "Proposed-v2 without slack reclamation"),
                           (LABEL["HEFT_DPMSR"], "HEFT placement + DPM-aware slack reclamation"),
                           ("EADAGS", "HEFT placement + lowest-frequency slack reclamation (EADAGS)")]:
            r = {m: paired(d, comp, "Proposed-v2", m) for m in ["total_energy_j", "edp_j_s"]}
            abl.append([key, name, fmt_ci(r["total_energy_j"]), fmt_ci(r["edp_j_s"]), f"{r['edp_j_s']['wins']}/{r['edp_j_s']['n']}"])
            N[f"abl_{key}_{comp}"] = r
    N["ablation_rows"] = abl
    for key in ["S2", "S3"]:
        d = D[key]
        N[f"place_only_{key}"] = paired(d, "HEFT", LABEL["ProposedV2-noSR"], "edp_j_s")
        N[f"sr_only_{key}"] = paired(d, "HEFT", LABEL["HEFT_DPMSR"], "edp_j_s")
        N[f"dpmsr_vs_lowest_{key}"] = paired(d, "EADAGS", LABEL["HEFT_DPMSR"], "edp_j_s")
        N[f"eadags_vs_heft_{key}"] = {m: paired(d, "HEFT", "EADAGS", m) for m in ["makespan_s", "total_energy_j", "edp_j_s"]}
        N[f"adapted_vs_heft_{key}"] = {m: paired(d, "HEFT", "EADAGS-adapted", m) for m in ["makespan_s", "total_energy_j", "edp_j_s"]}

    # ---------------- epsilon sweep --------------------------------------------------
    eps_rows, eps_plot = [], []
    for key, folder in [("S2", "S2_util"), ("S3", "S3_hetero_util")]:
        heft = D[key][D[key].scheduler == "HEFT"].set_index("workload_id")
        for e in ["0.05", "0.25", "0.5", "1.0"]:
            if e == "0.05":
                v = D[key][D[key].scheduler == "Proposed-v2"].set_index("workload_id")
            else:
                f = tidy(load_runs(raw / f"sweep_{key}_eps{e}"))
                v = f[f.dpm].set_index("workload_id")
            r, ci = {}, {}
            for m in ["makespan_s", "total_energy_j", "edp_j_s"]:
                pct = ((heft[m] - v[m]) / heft[m] * 100).dropna()
                r[m] = float(pct.mean())
                ci[m] = bootstrap_mean_ci(pct.to_numpy())
            eps_rows.append([key, e, f"{-r['makespan_s']:+.2f}", f"{r['total_energy_j']:+.2f}", f"{r['edp_j_s']:+.2f}"])
            eps_plot.append((key, float(e), -r["makespan_s"], r["total_energy_j"], r["edp_j_s"],
                             [-ci["makespan_s"][1], -ci["makespan_s"][0]], list(ci["total_energy_j"]), list(ci["edp_j_s"])))
    N["eps_rows"] = eps_rows
    N["eps"] = eps_plot

    # ---------------- E4 DPM sensitivity ---------------------------------------------
    e4 = []
    params = [("power.sleep_power_w", "Sleep power (W)", "11"), ("dpm.break_even_threshold_s", "Break-even time (s)", "13.25"),
              ("dpm.wake_up_delay_s", "Wake-up delay (s)", "10")]
    for par, lab, base in params:
        vals = sorted({p.split("=")[1] for p in os.listdir(raw) if p.startswith(f"E4_S2_{par}=")} | {base}, key=float)
        for val in vals:
            row = [lab, val + (" (default)" if val == base else "")]
            for key in ["S0", "S2"]:
                if val == base:
                    f = S[key].copy()
                    if key == "S0":  # E4 in S0 uses the 27 partial real-replay workloads
                        f = f[f["family"].eq("partial_real")]
                else:
                    f = tidy(load_runs(raw / f"E4_{key}_{par}={val}"))
                if f.empty:
                    row += ["", ""]
                    continue
                if key == "S0":
                    h = f[f.scheduler == "HEFT"]
                    nd = h[~h.dpm].set_index("workload_id")["total_energy_j"]
                    dp = h[h.dpm].set_index("workload_id")["total_energy_j"]
                    row.append(f"{((nd - dp) / nd * 100).mean():.1f}")
                    share = (h[h.dpm]["idle_sleep_energy_j"] / h[h.dpm]["total_energy_j"]).mean() * 100
                    row.append(f"{share:.2f}")
                else:
                    d = f[f.dpm]
                    rh = paired(d, "HEFT", "Proposed-v2", "edp_j_s")
                    re_ = paired(d, "EADAGS", "Proposed-v2", "edp_j_s")
                    row.append(f"{rh['mean']:+.2f}")
                    row.append(f"{re_['mean']:+.2f}")
            e4.append(row)
    N["e4_rows"] = e4
    s2h = [float(r[4]) for r in e4 if r[4] not in ("",)]
    s2e = [float(r[5]) for r in e4 if r[5] not in ("",)]
    N["e4_s2_heft_min"], N["e4_s2_heft_max"] = min(s2h), max(s2h)
    N["e4_s2_eads_min"], N["e4_s2_eads_max"] = min(s2e), max(s2e)
    sp = {r[1].split()[0]: float(r[2]) for r in e4 if r[0].startswith("Sleep")}
    N["e4_s0_sleep"] = sp

    # ---------------- E5 scalability -------------------------------------------------
    sc_path = raw / "E5_scalability" / "scalability_results.csv"
    if not sc_path.exists():
        sc_path = raw / "E5_scalability" / "scalability_results_partial.csv"
    sc = tidy(pd.read_csv(sc_path))
    sc["machines"] = sc["workload_id"].str.extract(r"_m(\d+)_").astype(int)
    sc["target"] = sc["target_task_count"]
    srows = []
    sc = sc[sc.dpm]
    complete = sc.groupby(["target", "machines", "seed"])["scheduler"].nunique()
    keep = complete[complete == sc["scheduler"].nunique()].index
    sc = sc.set_index(["target", "machines", "seed"]).loc[keep].reset_index()
    for (t, m), g in sc.groupby(["target", "machines"]):
        rt = g.groupby("scheduler")["scheduler_runtime_s"].mean()
        w = g.pivot_table(index="seed", columns="scheduler", values="edp_j_s")
        imp = ((w["HEFT"] - w["Proposed-v2"]) / w["HEFT"] * 100).mean() if "Proposed-v2" in w else np.nan
        impe = ((w["EADAGS"] - w["Proposed-v2"]) / w["EADAGS"] * 100).mean() if "EADAGS" in w else np.nan
        mk = g.pivot_table(index="seed", columns="scheduler", values="makespan_s")
        mkd = ((mk["Proposed-v2"] - mk["HEFT"]) / mk["HEFT"] * 100).mean()
        srows.append([str(t), str(m), f"{g['realised_task_count'].mean():.0f}", f"{rt.get('HEFT', np.nan):.2f}",
                      f"{rt.get('EADAGS', np.nan):.2f}", f"{rt.get('Proposed (v1)', np.nan):.2f}",
                      f"{rt.get('Proposed-v2', np.nan):.2f}", f"{imp:+.2f}", f"{impe:+.2f}", f"{mkd:+.2f}"])
    N["scal_rows"] = srows
    rt = sc[sc.dpm].groupby(["target", "machines", "scheduler"])["scheduler_runtime_s"].mean().unstack()
    N["scal_v1_ratio"] = [float((rt["Proposed (v1)"] / rt["Proposed-v2"]).min()), float((rt["Proposed (v1)"] / rt["Proposed-v2"]).max())]
    N["scal_v2_heft_ratio"] = [float((rt["Proposed-v2"] / rt["HEFT"]).min()), float((rt["Proposed-v2"] / rt["HEFT"]).max())]
    N["scal_imp_min"] = min(float(r[7]) for r in srows)
    N["scal_imp_max"] = max(float(r[7]) for r in srows)
    N["scal_mk_max"] = max(float(r[9]) for r in srows)
    N["scal_eads_min"] = min(float(r[8]) for r in srows)
    N["scal_max_tasks"] = int(sc["realised_task_count"].max())
    N["scal_complete"] = sc_path.name == "scalability_results.csv"
    sc.to_csv(out / "tables/scalability_tidy.csv", index=False)

    # ---------------- trace validation table ----------------------------------------
    tv = pd.read_csv(args.trace_validation / "trace_validation_statistics.csv")
    names = {"task_count": "Tasks per job", "edge_count": "Edges per job", "dag_depth": "DAG depth", "dag_width": "DAG width",
             "total_reference_runtime_s": "Total job runtime (s)", "median_task_runtime_s": "Median task runtime (s)",
             "total_instances": "Instances per job", "mean_cpu_fraction": "Mean CPU demand",
             "interarrival_time_s": "Job inter-arrival time (s)"}
    N["tv_rows"] = [[names[r.variable], f"{r.real_median:g}", f"{r.synthetic_median:g}", f"{r.ks_statistic:.3f}",
                     f"{r.ks_p_value:.3f}", f"{r.jensen_shannon_divergence:.4f}"]
                    for r in tv.itertuples() if r.variable in names]
    N["tv_max_ks"] = float(tv.ks_statistic.max())
    N["tv_min_p"] = float(tv.ks_p_value.min())

    # ---------------- figures ----------------------------------------------------------
    make_figures(out / "figures", S, D, N, sc, args.trace_validation)
    (out / "nums.json").write_text(json.dumps(N, indent=1, default=float))
    print("wrote", out)


def bar_label(ax, rects, fmt):
    for r in rects:
        ax.annotate(fmt(r.get_height()), (r.get_x() + r.get_width() / 2, r.get_height()), xytext=(0, 2),
                    textcoords="offset points", ha="center", va="bottom", fontsize=7, color=INK2)


def workflow_figure(fd):
    from matplotlib.patches import FancyBboxPatch
    fig, ax = plt.subplots(figsize=(7.2, 3.1))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 44)
    ax.axis("off")
    boxes = [
        (1, 25, 22, 17, "Alibaba trace", "Cluster Trace v2018:\nmachines, usage,\nbatch tasks, instances"),
        (26, 25, 22, 17, "Workloads", "500 jobs \u2192 108 frozen\nworkloads (27 replay,\n81 synthetic)"),
        (51, 25, 22, 17, "System models", "DVFS, power, DPM,\ncommunication;\n1 or 3 server classes"),
        (76, 25, 23, 17, "Schedulers", "HEFT, EADAGS,\nEADAGS-adapted,\nProposed v1 and v2"),
        (76, 2, 23, 17, "DPM replay", "one shared controller;\nfeasibility checks"),
        (51, 2, 22, 17, "Settings", "S0\u2013S3,\nDPM sensitivity (E4),\nscalability (E5)"),
        (26, 2, 22, 17, "Paired statistics", "bootstrap 95% CI,\nWilcoxon, Holm"),
        (1, 2, 22, 17, "Outputs", "makespan, energy\ncomponents, EDP,\nscheduling time"),
    ]
    for i, (x, y, w, h, title, sub) in enumerate(boxes):
        fill = "#eaf2fb" if i < 4 else "#fdf0ea"
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3,rounding_size=1.2", fc=fill, ec=INK2, lw=0.8))
        ax.text(x + w / 2, y + h - 3.2, title, ha="center", va="top", fontsize=8, fontweight="bold", color=INK)
        ax.text(x + w / 2, y + h - 7.6, sub, ha="center", va="top", fontsize=6.4, color=INK2, linespacing=1.25)
    arrow = dict(arrowstyle="-|>", color=INK2, lw=1.0)
    for x0, x1 in [(23.6, 25.6), (48.6, 50.6), (73.6, 75.6)]:
        ax.annotate("", xy=(x1, 33.5), xytext=(x0, 33.5), arrowprops=arrow)
    ax.annotate("", xy=(87.5, 19.6), xytext=(87.5, 24.4), arrowprops=arrow)
    for x0, x1 in [(75.4, 73.4), (50.4, 48.4), (25.4, 23.4)]:
        ax.annotate("", xy=(x1, 10.5), xytext=(x0, 10.5), arrowprops=arrow)
    fig.savefig(fd / "fig1_workflow.png", bbox_inches="tight")
    plt.close(fig)


def make_figures(fd, S, D, N, sc, tvdir):
    workflow_figure(fd)
    # Figure 2: trace validation ECDFs
    real = pd.read_csv(tvdir / "real_job_metrics.csv")
    syn = pd.read_csv(tvdir / "synthetic_job_metrics.csv")
    fig, axes = plt.subplots(1, 4, figsize=(7.2, 2.0))
    for ax, (col, lab, log) in zip(axes, [("task_count", "Tasks per job", True), ("dag_depth", "DAG depth", False),
                                          ("median_task_runtime_s", "Median task runtime (s)", True),
                                          ("interarrival_time_s", "Inter-arrival time (s)", True)]):
        for data, name, color, ls in [(real, "Alibaba trace", COLORS["HEFT"], "-"), (syn, "Synthetic", COLORS["EADAGS"], "--")]:
            v = np.sort(data[col].dropna().to_numpy())
            v = v[v > 0] if log else v
            ax.step(v, np.arange(1, len(v) + 1) / len(v), where="post", color=color, lw=1.6, ls=ls, label=name)
        if log:
            ax.set_xscale("log")
            ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
            ax.xaxis.set_major_locator(matplotlib.ticker.LogLocator(base=10, numticks=4))
        ax.tick_params(labelsize=7)
        ax.set_xlabel(lab, fontsize=8)
        ax.set_ylim(0, 1.02)
    axes[0].set_ylabel("ECDF")
    axes[0].legend(fontsize=7, loc="lower right")
    fig.tight_layout()
    fig.savefig(fd / "fig2_trace_validation.png")
    plt.close(fig)

    # Figure 3: S0 energy composition vs S2/S3 (100% stacked, parts of a whole) for HEFT-DPM
    comps = [("computation_energy_j", "Computation"), ("communication_energy_j", "Communication"),
             ("idle_sleep_energy_j", "Idle and sleep"), ("transition_energy_j", "Transitions")]
    ccols = ["#2a78d6", "#1baf7a", "#c9c8c2", "#eb6834"]
    fig, ax = plt.subplots(figsize=(6.0, 2.1))
    labels = {"S0": "S0: 200 machines,\n~7-day arrivals", "S1": "S1: S0 + server\nclasses",
              "S2": "S2: 10 machines,\ncompressed arrivals", "S3": "S3: S2 + server\nclasses"}
    ys = list(range(4))[::-1]
    for y, key in zip(ys, ["S0", "S1", "S2", "S3"]):
        h = D[key][D[key].scheduler == "HEFT"]
        tot = h["total_energy_j"].sum()
        left = 0
        for (c, name), col in zip(comps, ccols):
            share = h[c].sum() / tot * 100 if c in h else 0
            ax.barh(y, share, left=left, color=col, edgecolor="white", linewidth=1, height=0.6,
                    label=name if key == "S0" else None)
            if share > 8:
                ax.text(left + share / 2, y, f"{share:.1f}%", ha="center", va="center", fontsize=7,
                        color="white" if col != "#c9c8c2" else INK)
            left += share
    ax.set_yticks(ys)
    ax.set_yticklabels([labels[k] for k in ["S0", "S1", "S2", "S3"]], fontsize=7)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of total energy, HEFT-DPM (%)")
    ax.grid(axis="y", visible=False)
    ax.legend(ncol=4, fontsize=7, loc="upper center", bbox_to_anchor=(0.5, 1.28))
    fig.tight_layout()
    fig.savefig(fd / "fig3_energy_composition.png")
    plt.close(fig)

    # Figure 4: forest plot of Proposed-v2 paired EDP improvement (linear axis; v1 is off-scale and marked)
    comps4 = ["HEFT", "EADAGS", "EADAGS-adapted", "Proposed (v1)"]
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.3), sharey=True)
    xmax = 5.5
    for ax, key in zip(axes, ["S2", "S3"]):
        for i, c in enumerate(comps4):
            r = N[f"v2_vs_{c}_{key}"]["edp_j_s"]
            y = len(comps4) - 1 - i
            if r["mean"] > xmax:
                ax.annotate("", xy=(xmax, y), xytext=(xmax - 1.2, y),
                            arrowprops=dict(arrowstyle="-|>", color=COLORS[c], lw=1.8))
                ax.annotate(f"{r['mean']:+.1f}% (off scale)", (xmax - 1.3, y), xytext=(0, 7), textcoords="offset points",
                            ha="center", fontsize=7, color=INK2)
                continue
            ax.errorbar(r["mean"], y, xerr=[[r["mean"] - r["lo"]], [r["hi"] - r["mean"]]], fmt="o", ms=6,
                        color=COLORS[c], ecolor=COLORS[c], elinewidth=1.6, capsize=3)
            ax.annotate(f"{r['mean']:+.2f}%", (r["mean"], y), xytext=(0, 7), textcoords="offset points", ha="center",
                        fontsize=7, color=INK2)
        ax.axvline(0, color=INK2, lw=0.8)
        ax.set_title("S2: 10 machines, homogeneous" if key == "S2" else "S3: 10 machines, server classes", fontsize=8)
        ax.set_xlabel("EDP improvement of Proposed-v2 (%)")
        ax.set_xlim(-3, xmax)
        ax.set_ylim(-0.6, len(comps4) - 0.3)
        ax.grid(axis="y", visible=False)
    axes[0].set_yticks(range(len(comps4)))
    axes[0].set_yticklabels([f"vs {c}" for c in comps4[::-1]], fontsize=8)
    fig.tight_layout()
    fig.savefig(fd / "fig4_paired_edp.png")
    plt.close(fig)

    # Figure 5: makespan-energy trade-off relative to HEFT-DPM (S2 and S3)
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.7), sharey=True)
    for ax, key in zip(axes, ["S2", "S3"]):
        d = D[key]
        w = {m: d.pivot_table(index="workload_id", columns="scheduler", values=m) for m in ["makespan_s", "total_energy_j"]}
        for c in ORDER:
            if c not in w["makespan_s"]:
                continue
            mks = ((w["makespan_s"][c] - w["makespan_s"]["HEFT"]) / w["makespan_s"]["HEFT"] * 100).dropna()
            ens = ((w["total_energy_j"][c] - w["total_energy_j"]["HEFT"]) / w["total_energy_j"]["HEFT"] * 100).dropna()
            mk, en = mks.mean(), ens.mean()
            (mlo, mhi), (elo, ehi) = bootstrap_mean_ci(mks.to_numpy()), bootstrap_mean_ci(ens.to_numpy())
            ax.errorbar(mk, en, xerr=[[mk - mlo], [mhi - mk]], yerr=[[en - elo], [ehi - en]], fmt="none",
                        ecolor=COLORS[c], elinewidth=1.2, capsize=2.5, zorder=2)
            ax.scatter(mk, en, s=46, color=COLORS[c], edgecolor="white", linewidth=1.5, zorder=3)
            if c in ("EADAGS", "Proposed-v2"):
                # these two points nearly coincide; label them with leader lines
                target = (9, en + (1.6 if c == "EADAGS" else -1.6))
                ax.annotate(c, (mk, en), xytext=target, textcoords="data", fontsize=7, color=INK, va="center",
                            arrowprops=dict(arrowstyle="-", color=INK2, lw=0.6))
            else:
                ax.annotate(c, (mk, en), xytext=(6, 0), textcoords="offset points", fontsize=7, color=INK, va="center")
        ax.axhline(0, color=GRID, lw=0.8)
        ax.axvline(0, color=GRID, lw=0.8)
        ax.set_xlabel("Makespan change vs HEFT-DPM (%)")
        ax.set_title("S2: homogeneous" if key == "S2" else "S3: server classes", fontsize=8)
        ax.set_xlim(-3, 60)
    axes[0].set_ylabel("Energy change vs HEFT-DPM (%)")
    fig.tight_layout()
    fig.savefig(fd / "fig5_tradeoff.png")
    plt.close(fig)

    # Figure 6: epsilon sensitivity (S3); all three series share one unit (% vs HEFT-DPM)
    fig, ax = plt.subplots(figsize=(4.6, 2.5))
    e = [x for x in N["eps"] if x[0] == "S3"]
    xs = [x[1] for x in e]
    series = [("Makespan increase", [x[2] for x in e], [x[5] for x in e], COLORS["HEFT"], "o"),
              ("Energy saving", [x[3] for x in e], [x[6] for x in e], COLORS["EADAGS"], "s"),
              ("EDP improvement", [x[4] for x in e], [x[7] for x in e], COLORS["Proposed-v2"], "D")]
    for name, ys_, cis, col, mk in series:
        err = [[y - c[0] for y, c in zip(ys_, cis)], [c[1] - y for y, c in zip(ys_, cis)]]
        ax.errorbar(xs, ys_, yerr=err, color=col, lw=2, marker=mk, ms=6, markeredgecolor="white", capsize=3, elinewidth=1.2)
        ax.annotate(name, (xs[-1], ys_[-1]), xytext=(6, 0), textcoords="offset points", fontsize=7, color=INK, va="center")
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_xscale("log")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{x:g}" for x in xs])
    ax.set_xlabel("Placement slack ε (log scale)")
    ax.set_ylabel("Change vs HEFT-DPM (%)")
    ax.set_xlim(0.04, 2.6)
    fig.tight_layout()
    fig.savefig(fd / "fig6_epsilon.png")
    plt.close(fig)

    # Figure 7: scalability runtime (50 machines) and EDP improvement vs HEFT-DPM
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.6))
    m50 = sc[(sc.machines == 50) & sc.dpm]
    for c in ORDER:
        g = m50[m50.scheduler == c].groupby("realised_task_count")["scheduler_runtime_s"].mean()
        g = m50[m50.scheduler == c].groupby("target")[["realised_task_count", "scheduler_runtime_s"]].mean()
        if g.empty:
            continue
        axes[0].plot(g["realised_task_count"], g["scheduler_runtime_s"], color=COLORS[c], lw=2, marker="o", ms=5,
                     markeredgecolor="white", label=c)
    axes[0].set_yscale("log")
    axes[0].set_xscale("log")
    axes[0].set_xlabel("Tasks per workload (log scale)")
    axes[0].set_ylabel("Scheduling time (s, log scale)")
    axes[0].set_title("Scheduling time, 50 machines", fontsize=8)
    axes[0].legend(fontsize=6.5, loc="upper left")
    for axis in (axes[0].xaxis, axes[1].xaxis):
        axis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    axes[0].set_xticks([100, 250, 500, 1000])
    axes[0].set_xticklabels(["100", "250", "500", "1000"])
    for m, mk in [(10, "o"), (25, "s"), (50, "D")]:
        g = sc[(sc.machines == m) & sc.dpm]
        w = g.pivot_table(index=["target", "seed"], columns="scheduler", values="edp_j_s")
        if "Proposed-v2" not in w:
            continue
        imp = ((w["HEFT"] - w["Proposed-v2"]) / w["HEFT"] * 100).groupby("target").mean()
        axes[1].plot(imp.index, imp.values, marker=mk, lw=1.6, ms=5, color=["#2a78d6", "#eb6834", "#1baf7a"][[10, 25, 50].index(m)],
                     markeredgecolor="white", label=f"{m} machines")
    axes[1].axhline(0, color=INK2, lw=0.8)
    axes[1].set_xscale("log")
    axes[1].set_xticks([100, 250, 500, 1000])
    axes[1].set_xticklabels(["100", "250", "500", "1000"])
    axes[1].set_xlabel("Target tasks per workload")
    axes[1].set_ylabel("EDP improvement vs HEFT-DPM (%)")
    axes[1].set_title("Proposed-v2 EDP improvement", fontsize=8)
    axes[1].legend(fontsize=7, loc="best", framealpha=0.9)
    fig.tight_layout()
    fig.savefig(fd / "fig7_scalability.png")
    plt.close(fig)


if __name__ == "__main__":
    main()
