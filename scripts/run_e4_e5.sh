#!/bin/bash
# E5: scalability (100-1000 tasks x 10/25/50 machines x 3 seeds) and E4: DPM-parameter sensitivity.
cd "$(dirname "$0")/.."
D=${DATA_DIR:?set DATA_DIR to the Alibaba2018_Real_Subset folder}
O=${OUT_DIR:-outputs/revision_round2/raw}
mkdir -p $O
[ "${SKIP_E5:-0}" = 1 ] || ( python3 scripts/04_run_scalability.py --data-dir $D --output-dir $O/E5_scalability \
    --arrival-compression 0.001 \
    --schedulers HEFT EADAGS_adapted EADAGS_SR ProposedV2 Proposed > $O/E5.log 2>&1 ) &
(
  # S0 is limited to the 27 partial real-replay workloads (catalogue indices 0-26):
  # scheduler differences there are below 0.03% and 200-machine runs are slow.
  for setting in "S0|--start 0 --end 27" "S2|--machines 10 --arrival-compression 0.001"; do
    name=${setting%%|*}; flags=${setting#*|}
    for ov in power.sleep_power_w=5.5 power.sleep_power_w=22 power.sleep_power_w=44 \
              dpm.break_even_threshold_s=60 dpm.break_even_threshold_s=300 \
              dpm.wake_up_delay_s=1 dpm.wake_up_delay_s=60; do
      python3 scripts/12_run_revision_experiments.py --data-dir $D $flags \
        --output-dir $O/E4_${name}_${ov} --schedulers HEFT EADAGS_SR ProposedV2 --cfg $ov > /dev/null 2>>$O/errors.log
    done
  done
) &
wait
echo done > $O/E4E5_DONE
