#!/bin/bash
# Full revision experiment grid. Each setting runs the 108-workload catalogue.
cd "$(dirname "$0")/.."
D=${DATA_DIR:?set DATA_DIR to the Alibaba2018_Real_Subset folder}
O=${OUT_DIR:-outputs/revision_round2/raw}
run() { python3 scripts/12_run_revision_experiments.py --data-dir $D "$@" > /dev/null 2>>$O/errors.log; }
mkdir -p $O
# Fast schedulers first (both cores), then the slow original Proposed.
run --output-dir $O/S0_base   --schedulers HEFT EADAGS_adapted ProposedV2 &
run --output-dir $O/S1_hetero --hetero --schedulers HEFT EADAGS_adapted ProposedV2 &
wait
run --output-dir $O/S2_util      --machines 10 --arrival-compression 0.001 --schedulers HEFT EADAGS_adapted Proposed ProposedV2 &
run --output-dir $O/S3_hetero_util --hetero --machines 10 --arrival-compression 0.001 --schedulers HEFT EADAGS_adapted Proposed ProposedV2 &
wait
run --output-dir $O/S2_util_noSR      --machines 10 --arrival-compression 0.001 --schedulers ProposedV2 --set v2_slack_reclamation=0 &
run --output-dir $O/S3_hetero_util_noSR --hetero --machines 10 --arrival-compression 0.001 --schedulers ProposedV2 --set v2_slack_reclamation=0 &
wait
run --output-dir $O/S1_hetero_oldProposed --hetero --schedulers Proposed --start 0 --end 54 &
run --output-dir $O/S1_hetero_oldProposed --hetero --schedulers Proposed --start 54 --end 108 &
wait
echo ALLDONE > $O/DONE
