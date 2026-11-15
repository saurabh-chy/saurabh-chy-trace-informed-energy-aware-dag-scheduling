#!/bin/bash
# Sensitivity of Proposed-v2 to its placement slack epsilon (v2_slack_fraction).
cd "$(dirname "$0")/.."
D=${DATA_DIR:?set DATA_DIR to the Alibaba2018_Real_Subset folder}
O=${OUT_DIR:-outputs/revision_round2/raw}
for e in 0.25 0.5 1.0; do
  python3 scripts/12_run_revision_experiments.py --data-dir $D --output-dir $O/sweep_S2_eps$e --machines 10 --arrival-compression 0.001 --schedulers ProposedV2 --set v2_slack_fraction=$e > /dev/null 2>>$O/errors.log &
  python3 scripts/12_run_revision_experiments.py --data-dir $D --output-dir $O/sweep_S3_eps$e --hetero --machines 10 --arrival-compression 0.001 --schedulers ProposedV2 --set v2_slack_fraction=$e > /dev/null 2>>$O/errors.log &
  wait
done
echo done > $O/SWEEP_DONE
