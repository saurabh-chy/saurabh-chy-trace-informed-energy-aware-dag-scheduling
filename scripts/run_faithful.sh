#!/bin/bash
# Faithful EADAGS (EADAGS_SR) and the HEFT + DPM-aware-SR ablation in all four settings.
cd "$(dirname "$0")/.."
D=${DATA_DIR:?set DATA_DIR to the Alibaba2018_Real_Subset folder}
O=${OUT_DIR:-outputs/revision_round2/raw}
r() { python3 scripts/12_run_revision_experiments.py --data-dir $D --schedulers EADAGS_SR HEFT_DPMSR "$@" > /dev/null 2>>$O/errors.log; }
r --output-dir $O/S0_base_extra &
r --output-dir $O/S1_hetero_extra --hetero &
wait
r --output-dir $O/S2_util_extra --machines 10 --arrival-compression 0.001 &
r --output-dir $O/S3_hetero_util_extra --hetero --machines 10 --arrival-compression 0.001 &
wait
echo done > $O/FAITHFUL_DONE
