"""Build the supplementary document (Online Resource 1) from existing round-2 results.

Usage: python scripts/15_make_supplement.py --raw outputs/revision_round2/raw --out <file.docx>
No simulations are run; every value is computed from the result files.
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_s = importlib.util.spec_from_file_location("m14", ROOT / "scripts" / "14_make_revision_outputs.py")
m14 = importlib.util.module_from_spec(_s)
_s.loader.exec_module(m14)
paired, setting_frame, tidy, load_runs, COLORS = m14.paired, m14.setting_frame, m14.tidy, m14.load_runs, m14.COLORS
from reviewer_pipeline.statistics import bootstrap_mean_ci  # noqa: E402

SUB_NAMES = {"family": {"partial_real": "Partial real replay", "synthetic": "Synthetic"},
             "dag_size": {"small": "Small", "medium": "Medium", "large": "Large"},
             "arrival_condition": {"observed": "Observed", "light": "Light", "medium": "Medium", "heavy": "Heavy"},
             "communication_condition": {"low": "Low", "medium": "Medium", "high": "High"}}
DIM_LABEL = {"family": "Workload family", "dag_size": "DAG-size class", "arrival_condition": "Arrival condition",
             "communication_condition": "Communication scenario"}


def _f(v, d):
    t = f"{v:+.{d}f}"
    return t.replace("-", "+", 1) if float(t) == 0 else t


def ci_txt(r, d=2):
    return f"{_f(r['mean'], d)} [{_f(r['lo'], d)}, {_f(r['hi'], d)}]"


def load_settings(raw):
    S = {
        "S0": setting_frame(raw, [("S0_base", ""), ("S0_base_extra", "")]),
        "S1": setting_frame(raw, [("S1_hetero", ""), ("S1_hetero_extra", ""), ("S1_hetero_oldProposed", "")]),
        "S2": setting_frame(raw, [("S2_util", ""), ("S2_util_extra", ""), ("S2_util_noSR", "-noSR")]),
        "S3": setting_frame(raw, [("S3_hetero_util", ""), ("S3_hetero_util_extra", ""), ("S3_hetero_util_noSR", "-noSR")]),
    }
    old = pd.read_csv(ROOT / "outputs/common_dpm_full/common_dpm_results.csv")
    old = tidy(old[old["variant"].str.startswith("Proposed")])
    S["S0"] = pd.concat([S["S0"], old[[c for c in old.columns if c in S["S0"].columns]]], ignore_index=True)
    for k in S:
        S[k]["scheduler"] = S[k]["scheduler"].replace({"ProposedV2-noSR": "Proposed-v2 without SR"})
    return S


class Doc:
    def __init__(self):
        self.d = Document()
        sec = self.d.sections[0]
        sec.left_margin = sec.right_margin = Inches(0.8)
        sec.top_margin = sec.bottom_margin = Inches(0.8)
        st = self.d.styles["Normal"]
        st.font.name = "Times New Roman"
        st.font.size = Pt(11)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
        for n in ["Heading 1", "Heading 2", "Title"]:
            s = self.d.styles[n]
            s.font.name = "Times New Roman"
            s.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")

    def p(self, text, bold=False, italic=False, size=None, align=None, after=6):
        para = self.d.add_paragraph()
        r = para.add_run(text)
        r.bold, r.italic = bold, italic
        if size:
            r.font.size = Pt(size)
        if align:
            para.alignment = align
        para.paragraph_format.space_after = Pt(after)
        return para

    def caption(self, text):
        para = self.p(text, after=4)
        para.paragraph_format.keep_with_next = True
        para.runs[0].bold = False
        return para

    def table(self, header, rows, widths=None, size=8.5):
        t = self.d.add_table(rows=1, cols=len(header))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, h in enumerate(header):
            c = t.rows[0].cells[i]
            c.text = ""
            r = c.paragraphs[0].add_run(str(h))
            r.bold = True
            r.font.size = Pt(size)
            sh = OxmlElement("w:shd")
            sh.set(qn("w:val"), "clear")
            sh.set(qn("w:color"), "auto")
            sh.set(qn("w:fill"), "D9EAF7")
            c._tc.get_or_add_tcPr().append(sh)
        trpr = t.rows[0]._tr.get_or_add_trPr()
        th = OxmlElement("w:tblHeader")
        trpr.append(th)
        for row in rows:
            cells = t.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = ""
                r = cells[i].paragraphs[0].add_run(str(v))
                r.font.size = Pt(size)
        if widths:
            tot = sum(widths)
            avail = 6.9
            t.autofit = False
            lay = OxmlElement("w:tblLayout")
            lay.set(qn("w:type"), "fixed")
            t._tbl.tblPr.append(lay)
            for i, gc in enumerate(t._tbl.tblGrid.findall(qn("w:gridCol"))):
                gc.set(qn("w:w"), str(int(1440 * avail * widths[i] / tot)))
            for row in t.rows:
                for i, w in enumerate(widths):
                    row.cells[i].width = Inches(avail * w / tot)
        self.d.add_paragraph().paragraph_format.space_after = Pt(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=ROOT / "outputs/revision_round2/raw")
    ap.add_argument("--trace-validation", type=Path, default=ROOT / "outputs/trace_validation")
    ap.add_argument("--out", type=Path, default=ROOT / "outputs/revision_round2/Online_Resource_1_Supplementary_Material.docx")
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    S = load_settings(a.raw)
    D = {k: v[v["dpm"]] for k, v in S.items()}
    doc = Doc()
    H = lambda t, l=1: doc.d.add_heading(t, level=l)  # noqa: E731
    tn = iter(range(1, 50))
    fn = iter(range(1, 10))

    doc.p("Online Resource 1", bold=True, size=16, align=WD_ALIGN_PARAGRAPH.CENTER, after=2)
    doc.p("Trace-Informed Energy-Aware Scheduling for DAG Workloads in Distributed Cloud Systems", italic=True,
          align=WD_ALIGN_PARAGRAPH.CENTER, after=2)
    doc.p("Supplementary material", align=WD_ALIGN_PARAGRAPH.CENTER, after=12)
    doc.p("This Online Resource supports the article with detailed tables, a distribution figure, and the per-workload "
          "result files. It is supplied as the archive Online_Resource_1.zip, which contains this document and the data files "
          "described in Section S10. All values are computed from those result files. Table and figure numbers prefixed with S "
          "refer to this document; numbers without a prefix refer to the article. Unless a caption states otherwise, paired "
          "differences are expressed so that positive values favour Proposed-v2. Intervals are bootstrap 95% confidence "
          "intervals of the mean of per-workload paired percentage differences (2,000 resamples).")
    H("Contents", 2)
    for item in ["S1. Workload catalogue", "S2. Trace validation: full statistics",
                 "S3. Mean results for every scheduler and setting, energy components, and Proposed (v1)",
                 "S4. Paired comparisons in the low-utilisation settings S0 and S1",
                 "S5. Results by workload subgroup in the loaded settings S2 and S3",
                 "S6. Distribution of per-workload EDP differences",
                 "S7. Placement-slack sensitivity with confidence intervals",
                 "S8. DPM-parameter sensitivity: full results",
                 "S9. Scalability: results per configuration",
                 "S10. Data files and reproduction"]:
        doc.p(item, after=1)

    # S1 catalogue
    H("S1. Workload catalogue")
    man = D["S2"][D["S2"].scheduler == "HEFT"][["workload_id", "family", "dag_size", "arrival_condition",
                                                  "communication_condition", "seed", "task_count", "edge_count"]].drop_duplicates("workload_id")
    rows = []
    for (fam, size, arr), g in man.groupby(["family", "dag_size", "arrival_condition"]):
        rows.append([SUB_NAMES["family"].get(fam, fam), SUB_NAMES["dag_size"].get(size, size),
                     SUB_NAMES["arrival_condition"].get(arr, arr), len(g), f"{g.task_count.min()}–{g.task_count.max()}",
                     f"{g.task_count.mean():.1f}", f"{g.edge_count.mean():.1f}"])
    rows.append(["Total", "", "", len(man), f"{man.task_count.min()}–{man.task_count.max()}",
                 f"{man.task_count.mean():.1f}", f"{man.edge_count.mean():.1f}"])
    doc.caption(f"Table S{next(tn)}. Composition of the 108 workloads (each cell has three communication scenarios × "
                "three seeds 42, 101, 202, except partial real replay, which uses observed arrivals)")
    doc.table(["Family", "DAG size", "Arrival", "Workloads", "Tasks (range)", "Mean tasks", "Mean edges"], rows,
              [1.4, 0.8, 0.9, 0.8, 1.0, 0.8, 0.8])

    # S2 trace validation
    H("S2. Trace validation: full statistics")
    tv = pd.read_csv(a.trace_validation / "trace_validation_statistics.csv")
    names = {"task_count": "Tasks per job", "edge_count": "Edges per job", "dag_depth": "DAG depth", "dag_width": "DAG width",
             "total_reference_runtime_s": "Total job runtime (s)", "median_task_runtime_s": "Median task runtime (s)",
             "total_instances": "Instances per job", "mean_cpu_fraction": "Mean CPU demand",
             "interarrival_time_s": "Job inter-arrival time (s)",
             "edge_density": "Edge density", "mean_memory_fraction": "Mean memory demand"}
    cols = [c for c in ["real_median", "synthetic_median", "ks_statistic", "ks_p_value", "jensen_shannon_divergence",
                        "wasserstein_distance"] if c in tv.columns]
    rows = [[names.get(r["variable"], r["variable"])] + [f"{r[c]:.4g}" for c in cols] for _, r in tv.iterrows()]
    doc.caption(f"Table S{next(tn)}. Alibaba trace jobs versus trace-informed synthetic jobs (500 each); extends Table 11")
    doc.table(["Property", "Trace median", "Synthetic median", "KS statistic", "KS p-value", "JS divergence",
               "Wasserstein"][:1 + len(cols)], rows, [1.6, 0.9, 0.9, 0.8, 0.8, 0.9, 0.9])

    # S3 means
    H("S3. Mean results for every scheduler and setting")
    doc.p("Table S3 lists mean values over the 108 workloads under common DPM, including the ablation variants, and splits "
          "total energy into its components. Energy components are in MJ for S0 and S1 and in kJ for S2 and S3.")
    order = ["HEFT", "EADAGS", "EADAGS-adapted", "Proposed (v1)", "Proposed-v2", "Proposed-v2 without SR", "HEFT + DPM-aware SR"]
    rows = []
    for k in ["S0", "S1", "S2", "S3"]:
        unit, f = ("MJ", 1e6) if k in ("S0", "S1") else ("kJ", 1e3)
        for s in order:
            x = D[k][D[k].scheduler == s]
            if x.empty:
                continue
            rows.append([k, "EADAGS (faithful)" if s == "EADAGS" else s, len(x), f"{x.makespan_s.mean():,.1f}", f"{x.computation_energy_j.mean() / f:,.3f}",
                         f"{x.communication_energy_j.mean() / f:,.4f}", f"{x.idle_sleep_energy_j.mean() / f:,.3f}",
                         f"{x.transition_energy_j.mean() / f:,.4f}", f"{x.total_energy_j.mean() / f:,.3f} {unit}",
                         f"{x.edp_j_s.mean():.4e}", f"{x.utilisation_fraction.mean() * 100:.1f}", f"{x.scheduling_runtime_s.mean():.3f}" + (" \u2020" if (k == "S0" and s == "Proposed (v1)") else "")])
    doc.caption(f"Table S{next(tn)}. Mean results under common DPM (n = workloads per row; Comp. = computation, Comm. = communication, Trans. = state transitions, Util. = mean machine utilisation, Sched. time = scheduling time)")
    doc.table(["Setting", "Scheduler", "n", "Makespan (s)", "Comp.", "Comm.", "Idle + sleep",
               "Trans.", "Total energy", "EDP (J·s)", "Util. (%)", "Sched. time (s)"], rows,
              [0.55, 1.2, 0.35, 0.8, 0.75, 0.6, 0.75, 0.55, 0.85, 0.8, 0.5, 0.6], size=7)
    doc.p("\u2020 Measured with Python memory tracing enabled (about 13-fold slower); it is not comparable with the other scheduling times. In S1, measured without tracing, Proposed (v1) "
          "needs 13.6 s against 1.0 s for HEFT.", size=9)

    rows = []
    for k in ["S0", "S1", "S2", "S3"]:
        r = {m: paired(D[k], "HEFT", "Proposed (v1)", m) for m in ["makespan_s", "total_energy_j", "edp_j_s"]}
        rows.append([k, r["edp_j_s"]["n"], ci_txt({q: -r["makespan_s"][q] for q in ("mean", "hi", "lo")} | {"lo": -r["makespan_s"]["hi"], "hi": -r["makespan_s"]["lo"]}),
                     ci_txt({"mean": -r["total_energy_j"]["mean"], "lo": -r["total_energy_j"]["hi"], "hi": -r["total_energy_j"]["lo"]}),
                     ci_txt({"mean": -r["edp_j_s"]["mean"], "lo": -r["edp_j_s"]["hi"], "hi": -r["edp_j_s"]["lo"]})])
    doc.p("Table S4 reports the initial integrated heuristic, Proposed (v1), relative to HEFT-DPM. In the loaded settings it "
          "uses less energy but lengthens makespan enough to worsen EDP (article Sections 5 and 6.3).")
    doc.caption(f"Table S{next(tn)}. Proposed (v1) relative to HEFT-DPM under common DPM (paired % change, mean [95% CI]; "
                "positive = higher than HEFT-DPM)")
    doc.table(["Setting", "n", "Makespan change", "Energy change", "EDP change"], rows, [0.6, 0.4, 1.6, 1.6, 1.6])

    # S4 S0/S1 paired
    H("S4. Paired comparisons in the low-utilisation settings")
    rows = []
    for k in ["S0", "S1"]:
        for c in ["HEFT", "EADAGS", "EADAGS-adapted", "Proposed (v1)"]:
            if c not in set(D[k].scheduler):
                continue
            r = {m: paired(D[k], c, "Proposed-v2", m) for m in ["makespan_s", "total_energy_j", "edp_j_s"]}
            rows.append([k, "EADAGS (faithful)" if c == "EADAGS" else c, ci_txt(r["makespan_s"], 4), ci_txt(r["total_energy_j"], 4), ci_txt(r["edp_j_s"], 4),
                         f"{r['edp_j_s']['wins']}/{r['edp_j_s']['n']}"])
    doc.caption(f"Table S{next(tn)}. Proposed-v2 versus each comparator in S0 and S1 (%, mean [95% CI]; makespan as reduction)")
    doc.table(["Setting", "Comparator", "Makespan", "Energy", "EDP", "EDP wins"], rows, [0.6, 1.2, 1.8, 1.8, 1.8, 0.7])
    doc.p("Differences in S0 and S1 are below 0.01% for schedulers that keep HEFT's makespan; the larger difference to "
          "Proposed (v1) comes from its longer makespan (article Section 6.2).")

    # S5 subgroups
    H("S5. Results by workload subgroup in the loaded settings")
    doc.p("Tables S6 and S7 break the main comparison (article Table 14) down by workload family, DAG size, arrival "
          "condition, and communication scenario. Subgroups contain 9 to 81 workloads, so their intervals are wider than "
          "those of the full comparison.")
    for k in ["S2", "S3"]:
        rows = []
        d = D[k]
        for dim in ["family", "dag_size", "arrival_condition", "communication_condition"]:
            for val, g in d.groupby(dim):
                ids = g.workload_id.unique()
                sub = d[d.workload_id.isin(ids)]
                rh = paired(sub, "HEFT", "Proposed-v2", "edp_j_s")
                re_ = paired(sub, "EADAGS", "Proposed-v2", "edp_j_s")
                ra = paired(sub, "EADAGS-adapted", "Proposed-v2", "edp_j_s")
                rows.append([DIM_LABEL[dim], SUB_NAMES[dim].get(val, val), rh["n"], ci_txt(rh), ci_txt(re_), ci_txt(ra)])
        doc.caption(f"Table S{next(tn)}. Setting {k}: EDP improvement of Proposed-v2 by subgroup (%, mean [95% CI])")
        doc.table(["Dimension", "Subgroup", "n", "vs HEFT-DPM", "vs EADAGS (faithful)", "vs EADAGS-adapted"], rows,
                  [1.4, 1.1, 0.4, 1.4, 1.4, 1.4])

    # S6 distribution figure
    H("S6. Distribution of per-workload EDP differences")
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.8), sharey=False)
    for ax, k in zip(axes, ["S2", "S3"]):
        w = D[k].pivot_table(index="workload_id", columns="scheduler", values="edp_j_s")
        data, labels = [], []
        for c in ["HEFT", "EADAGS", "EADAGS-adapted"]:
            data.append(((w[c] - w["Proposed-v2"]) / w[c] * 100).dropna().to_numpy())
            labels.append("vs EADAGS (faithful)" if c == "EADAGS" else f"vs {c}")
        parts = ax.boxplot(data, orientation="horizontal", widths=0.5, patch_artist=True, showfliers=True,
                           flierprops=dict(marker="o", markersize=3, markerfacecolor="#52514e", markeredgecolor="none"))
        for patch, c in zip(parts["boxes"], ["HEFT", "EADAGS", "EADAGS-adapted"]):
            patch.set_facecolor(COLORS[c])
            patch.set_alpha(0.55)
        ax.set_yticks([1, 2, 3])
        ax.set_yticklabels(labels, fontsize=8)
        ax.axvline(0, color="#52514e", lw=0.8)
        ax.set_xlabel("Per-workload EDP improvement of Proposed-v2 (%)")
        ax.set_title(f"{k} (n = 108 workloads)", fontsize=8)
    fig.tight_layout()
    fpath = a.out.parent / "figS1_edp_distribution.png"
    fig.savefig(fpath, dpi=300)
    plt.close(fig)
    doc.d.add_picture(str(fpath), width=Inches(6.8))
    doc.p(f"Figure S{next(fn)}. Distribution of per-workload paired EDP differences between Proposed-v2 and each "
          "comparator in S2 and S3 (n = 108 workloads each). Boxes show the interquartile range and median, whiskers extend "
          "to 1.5 times the interquartile range, and points are individual workloads beyond the whiskers. Positive values "
          "favour Proposed-v2.", italic=True)

    # S7 epsilon
    H("S7. Placement-slack sensitivity")
    rows = []
    for k, folder in [("S2", "S2_util"), ("S3", "S3_hetero_util")]:
        heft = D[k][D[k].scheduler == "HEFT"].set_index("workload_id")
        for e in ["0.05", "0.25", "0.5", "1.0"]:
            if e == "0.05":
                v = D[k][D[k].scheduler == "Proposed-v2"].set_index("workload_id")
            else:
                f_ = tidy(load_runs(a.raw / f"sweep_{k}_eps{e}"))
                v = f_[f_.dpm].set_index("workload_id")
            out = [k, e]
            for m, sign in [("makespan_s", -1), ("total_energy_j", 1), ("edp_j_s", 1)]:
                pct = ((heft[m] - v[m]) / heft[m] * 100).dropna() * sign
                lo, hi = bootstrap_mean_ci(pct.to_numpy())
                out.append(f"{pct.mean():+.2f} [{lo:+.2f}, {hi:+.2f}]")
            rows.append(out)
    doc.caption(f"Table S{next(tn)}. Proposed-v2 with placement slack ε relative to HEFT-DPM (%, mean [95% CI]; n = 108); "
                "extends Table 16")
    doc.table(["Setting", "ε", "Makespan increase", "Energy saving", "EDP improvement"], rows, [0.6, 0.5, 1.6, 1.6, 1.6])

    # S8 E4
    H("S8. DPM-parameter sensitivity")
    rows = []
    params = [("power.sleep_power_w", "Sleep power (W)"), ("dpm.break_even_threshold_s", "Break-even time (s)"),
              ("dpm.wake_up_delay_s", "Wake-up delay (s)")]
    for par, lab in params:
        for p_ in sorted([p for p in a.raw.iterdir() if p.name.startswith(f"E4_S2_{par}=")], key=lambda p: float(p.name.split("=")[1])):
            val = p_.name.split("=")[1]
            f_ = tidy(load_runs(p_))
            d = f_[f_.dpm]
            r = {c: {m: paired(d, c, "Proposed-v2", m) for m in ["total_energy_j", "edp_j_s"]} for c in ["HEFT", "EADAGS"]}
            s0 = tidy(load_runs(a.raw / f"E4_S0_{par}={val}"))
            h = s0[s0.scheduler == "HEFT"]
            nd = h[~h.dpm].set_index("workload_id")["total_energy_j"]
            dp = h[h.dpm].set_index("workload_id")["total_energy_j"]
            rows.append([lab, val, f"{((nd - dp) / nd * 100).mean():.1f}", ci_txt(r["HEFT"]["total_energy_j"]),
                         ci_txt(r["HEFT"]["edp_j_s"]), ci_txt(r["EADAGS"]["edp_j_s"])])
    doc.caption(f"Table S{next(tn)}. One-at-a-time DPM-parameter variation (S0: 27 partial real-replay workloads; S2: 108 "
                "workloads; %, mean [95% CI]); default values are in article Table 17")
    doc.table(["Parameter", "Value", "S0: DPM saving, HEFT", "S2: v2 vs HEFT, energy", "S2: v2 vs HEFT, EDP",
               "S2: v2 vs EADAGS (faithful), EDP"], rows, [1.3, 0.6, 1.0, 1.5, 1.5, 1.5])

    # S9 E5
    H("S9. Scalability")
    sc = tidy(pd.read_csv(a.raw / "E5_scalability" / "scalability_results.csv"))
    sc = sc[sc.dpm]
    sc["machines"] = sc["workload_id"].str.extract(r"_m(\d+)_").astype(int)
    rows = []
    for (t, m), g in sc.groupby(["target_task_count", "machines"]):
        for s in ["HEFT", "EADAGS", "EADAGS-adapted", "Proposed (v1)", "Proposed-v2"]:
            x = g[g.scheduler == s]
            if x.empty:
                continue
            rows.append([t, m, f"{x.realised_task_count.mean():.0f}", "EADAGS (faithful)" if s == "EADAGS" else s, f"{x.scheduler_runtime_s.mean():.2f} ± {x.scheduler_runtime_s.std():.2f}",
                         f"{x.makespan_s.mean():,.0f}", f"{x.total_energy_j.mean() / 1e3:,.1f}", f"{x.edp_j_s.mean():.3e}"])
    doc.caption(f"Table S{next(tn)}. Scalability per configuration (mean ± standard deviation over three seeds for "
                "scheduling time; means for the other columns). Times include Python memory-tracing overhead and are "
                "comparable only within this table")
    doc.table(["Target tasks", "Machines", "Realised tasks", "Scheduler", "Scheduling time (s)", "Makespan (s)",
               "Energy (kJ)", "EDP (J·s)"], rows, [0.7, 0.7, 0.7, 1.3, 1.3, 1.0, 0.9, 1.0], size=7.5)

    # S10 data files
    H("S10. Data files and reproduction")
    doc.p("Besides this document, the archive Online_Resource_1.zip contains the following folders.")
    for item in [
        "per_workload_results/: one CSV per experiment and scheduler group (S0–S3, ablation, slack and DPM-parameter sensitivity, scalability); "
        "one row per workload, scheduler, and DPM state, with makespan, energy components, EDP, utilisation, scheduling time, "
        "candidate evaluations, and workload attributes.",
        "summary_tables/: the aggregated tables used in the article.",
        "trace_validation/: job-level metrics of the trace subset and of the synthetic jobs, and the test statistics.",
        "README.txt: column definitions and the mapping from scheduler identifiers in the files to the names in the article "
        "(EADAGS_SR = EADAGS (faithful); EADAGS_adapted = EADAGS-adapted; Proposed = Proposed (v1); ProposedV2 = Proposed-v2; "
        "HEFT_DPMSR = HEFT + DPM-aware slack reclamation).",
    ]:
        doc.d.add_paragraph(item, style="List Bullet")
    doc.p("All schedulers, including Proposed (v1), use the same workload catalogue, configuration, and simulation "
          "pipeline. The simulator, configuration files, and "
          "scripts that regenerate every table and figure in the article and in this document are described in the article's "
          "Code availability statement (this document is produced by scripts/15_make_supplement.py).")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    doc.d.save(a.out)
    print("saved", a.out)


if __name__ == "__main__":
    main()
