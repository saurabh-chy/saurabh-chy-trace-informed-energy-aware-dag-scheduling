# Reproducing the round-2 manuscript

This repository reproduces every experiment, table, and figure in
*Trace-Informed Energy-Aware Scheduling for DAG Workloads in Distributed Cloud Systems* (revised, round 2).

## Contents

The code reproduces the round-2 manuscript from a derived subset of Alibaba Cluster Trace v2018. Raw per-workload
results for every setting are in `outputs/revision_round2/raw/`, paired statistics in `outputs/revision_round2/analysis/`,
and every number, table, and figure used in the manuscript in `outputs/revision_round2/manuscript/` (`nums.json`,
`tables/`, `figures/`).

## Requirements

- Python 3.13 (results were produced with Python 3.13, pandas 3.0, NumPy 2.5, SciPy 1.18, NetworkX 3.7, Matplotlib 3.11)
- `pip install -r requirements.txt` (exact versions pinned)
- The derived input subset (see "Input data" below).

## Input data

Alibaba Cluster Trace v2018 is released by Alibaba "for research or study purposes" after a short download survey
(https://github.com/alibaba/clusterdata, `cluster-trace-v2018/trace_2018.md`). No explicit redistribution licence is
stated, so the derived subset is **not** included here. To reconstruct it:

1. Download `batch_task`, `batch_instance`, `machine_meta`, and `machine_usage` (or the single
   `alibaba_clusterdata_v2018.tar.gz`) from the official links and verify them against the SHA-256 values published in
   `trace_2018.md`.
2. Run the included Stage 1-2 extraction tool on Windows PowerShell. It is stored at
   `data_manifest/create_real_subset_FIXED.ps1`. Place `batch_task.csv` and `machine_meta.csv`, plus either the
   extracted `batch_instance.csv` / `machine_usage.csv` files or their `.tar` / `.tar.gz` archives, under a base
   folder. For example, from the repository root:

   ```powershell
   powershell.exe -ExecutionPolicy Bypass -File .\data_manifest\create_real_subset_FIXED.ps1 `
     -Stage all -BasePath "D:\AlibabaTrace" -MaximumJobs 500 `
     -MaximumPlannedInstances 250000 -MachineUsageMachineLimit 200
   ```

   The tool writes `D:\AlibabaTrace\Alibaba2018_Real_Subset\`. Stage 1 selects complete jobs, reconstructs DAG
   edges, filters valid instance rows, and selects machine metadata. Stage 2 filters machine-usage records for
   the selected machines. To run the stages separately, use `-Stage 1` first and then `-Stage 2` with the same
   base folder. The generated `real_trace_manifest_stage1.json` and `real_trace_manifest_stage2.json` record the
   parameters and row counts. The selected identifiers shipped in `data_manifest/` are the expected IDs for this
   subset. The pipeline reads the six CSV files listed in the checksum manifest.
3. From the generated `Alibaba2018_Real_Subset` directory, verify the six output CSVs with
   `data_manifest/SHA256SUMS_input_subset.txt`. The selected-ID files in `data_manifest/` are also checksummed there.
   The extraction script itself is checksummed in `data_manifest/SHA256SUMS_code.txt`.

```
a4210dad07c70da6a3e54bd6a0e168b35c17abdcc16e426ebdb15150160c9931  real_jobs.csv
7164b97ef49ec14e155ebbaf24541dd3c4a7dd7011f5a4e46d16684a04277aa5  real_tasks.csv
81e17ae8a11a9819dd73d66c0fd9c621176655cd4cc094cfc25dc9a9d1bbd953  real_dag_edges.csv
1e69ecfeb6e60483cd781d42aabfb29bc7b3081011939cb008aacbd6e4254741  real_instances.csv
8eba62ba37357f09e79b878bb49fa3866e7a3db2ca548ceedf677ad7c9be5a65  real_machine_capacities.csv
c7a48fdeb3ed3783413f0533d0884691da2e5ceca80ea650fbeb01022463ee03  real_machine_usage_summary.csv
47f64174bdb74763606e5992f4ca21e7885e673ed519fbef42479eb68dfa60fb  identifier1_selected_job_ids.txt
b99f4231bf806bf6c3a52652dde133f16b7438d71982fc255ba3e5bb7865bd11  selected_machine_ids.txt (shipped as "#U206fidentifier2_selected_machine_ids.txt" in the authors' copy)
```

Expected counts: 500 jobs, 2,223 tasks, 1,798 dependency edges, 200 machines (one nominal class: 96 CPU and 100 memory units).

## One command

```bash
DATA_DIR=/path/to/Alibaba2018_Real_Subset bash scripts/reproduce_all.sh
```

All runs are deterministic (seeds 42, 101, 202 for workload construction; fixed seed 2026 for machine subsets and
2,000-resample bootstrap with seed 42). Scheduling-time values depend on hardware; all other numbers reproduce exactly.
On a 2-vCPU machine the full run takes about 6–8 hours, dominated by Proposed (v1) at 1,000 tasks.

## What each script produces

| Script | Purpose | Manuscript items |
|---|---|---|
| `scripts/01_validate_inputs.py` | Checks the subset files | — |
| `scripts/03_trace_validation.py` | Trace vs synthetic job distributions (KS, JS, Wasserstein) | Table 11, Figure 2 |
| `scripts/09_environment_report.py` | Python, library, and hardware versions | Section 4.4 |
| `scripts/02_run_common_dpm_baselines.py` | Original common-DPM run (S0, incl. Proposed v1) | Table 12 (Proposed v1 row) |
| `scripts/12_run_revision_experiments.py` | Main experiment driver: settings S0–S3, server classes, overrides | Tables 12–17 |
| `scripts/run_all.sh` | S0–S3 with HEFT, EADAGS-adapted, Proposed v1, Proposed-v2, and Proposed-v2 without SR | Tables 12–15 |
| `scripts/run_faithful.sh` | Faithful EADAGS and HEFT + DPM-aware slack reclamation | Tables 13–15 |
| `scripts/run_sweep.sh` | Placement slack ε ∈ {0.25, 0.5, 1.0} | Table 16, Figure 6 |
| `scripts/run_e4_e5.sh` | DPM-parameter sensitivity (E4) and scalability to 1,000 tasks (E5) | Tables 17–18, Figure 7 |
| `scripts/13_analyze_revision_experiments.py` | Paired statistics per setting (bootstrap CI, Wilcoxon, Holm) | Table 14 |
| `scripts/14_make_revision_outputs.py` | Every number, table, and figure used in the text (`nums.json`, `tables/`, `figures/`) | All tables and Figures 1–7 |

## Where the algorithms are

| Scheduler | Code (`reviewer_pipeline/schedulers.py`) |
|---|---|
| HEFT | `HEFTScheduler` |
| EADAGS (faithful: EFT placement + original slack rule) | `EADAGSFaithfulScheduler` (`--schedulers EADAGS_SR`) |
| EADAGS-adapted (previous version; 5% absolute-time slack) | `EADAGSAdaptedScheduler` |
| Proposed (v1) | `ProposedScheduler` |
| Proposed-v2 | `ProposedV2Scheduler` and `reclaim_slack()` |
| Ablation: HEFT + DPM-aware slack reclamation | `HEFTDPMAwareSRScheduler` (`--schedulers HEFT_DPMSR`) |

The common DPM replay is `reviewer_pipeline/dpm.py::replay_with_common_dpm`; the power, execution, and communication
models are in `reviewer_pipeline/energy.py`; model parameters are in `configs/experiment_config.json`. Modelled
server classes are defined in `scripts/12_run_revision_experiments.py` (`MACHINE_CLASSES`).

## Settings

| Setting | Flags to script 12 |
|---|---|
| S0 | (none) |
| S1 | `--hetero` |
| S2 | `--machines 10 --arrival-compression 0.001` |
| S3 | `--hetero --machines 10 --arrival-compression 0.001` |

## Provenance of the Proposed (v1) results in S0

The Proposed (v1) rows of setting S0 (manuscript Table 12) come from `outputs/common_dpm_full/common_dpm_results.csv`,
produced by `scripts/02_run_common_dpm_baselines.py` before the round-2 schedulers were added. Script 14 merges them
into S0. They were checked against the round-2 S0 runs (`outputs/revision_round2/raw/S0_base/`): the 108 workload IDs
are identical, and the HEFT and EADAGS-adapted results present in both runs (task and edge counts, makespan, energy,
EDP; DPM and no DPM; 432 rows) are identical to machine precision. Both runs use `configs/experiment_config.json`
unchanged. All other settings were run with Proposed (v1) directly by `scripts/12_run_revision_experiments.py`.

## Note on scheduling times

Script 04 (scalability) measures scheduling time with Python memory tracing enabled, which slows execution roughly
13-fold. Its times are comparable only with each other. Script 12 measures time without tracing.
