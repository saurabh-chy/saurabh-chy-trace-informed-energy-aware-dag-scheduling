#!/bin/bash
# Reproduce every experiment, table, and figure of the round-2 manuscript.
#   DATA_DIR=/path/to/Alibaba2018_Real_Subset bash scripts/reproduce_all.sh
# Results go to outputs/revision_round2/ (raw runs, analysis, manuscript tables/figures).
# Runtime on a 2-vCPU machine: roughly 6-8 hours, dominated by Proposed (v1) at 1,000 tasks.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DATA_DIR:?set DATA_DIR to the Alibaba2018_Real_Subset folder}"
export DATA_DIR
export OUT_DIR=${OUT_DIR:-outputs/revision_round2/raw}
mkdir -p "$OUT_DIR"

python3 scripts/01_validate_inputs.py --data-dir "$DATA_DIR" --output-dir outputs/input_validation
python3 scripts/03_trace_validation.py --data-dir "$DATA_DIR" --output-dir outputs/trace_validation
python3 scripts/09_environment_report.py --output-dir outputs/environment
# Published common-DPM run (S0 incl. Proposed v1); reproduces the previous version's Tables 27-33 exactly.
python3 scripts/02_run_common_dpm_baselines.py --data-dir "$DATA_DIR" --output-dir outputs/common_dpm_full
bash scripts/run_all.sh        # S0-S3 main settings, ablation (no-SR), Proposed v1 in S1
bash scripts/run_faithful.sh   # faithful EADAGS and HEFT + DPM-aware SR in S0-S3
bash scripts/run_sweep.sh      # placement-slack (epsilon) sensitivity in S2, S3
bash scripts/run_e4_e5.sh      # E4 DPM-parameter sensitivity, E5 scalability
python3 scripts/13_analyze_revision_experiments.py --root "$OUT_DIR" --output-dir outputs/revision_round2/analysis
python3 scripts/14_make_revision_outputs.py --raw "$OUT_DIR" --trace-validation outputs/trace_validation \
    --out outputs/revision_round2/manuscript
echo "All outputs written to outputs/revision_round2/"
