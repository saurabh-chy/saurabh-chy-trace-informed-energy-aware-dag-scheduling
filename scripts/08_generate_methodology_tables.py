from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate notation, parameter, and complexity tables for the manuscript.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    config = json.loads(args.config.read_text(encoding="utf-8"))

    notation = [
        ("G=(V,E)", "Workflow directed acyclic graph", "-"),
        ("V", "Set of tasks", "-"),
        ("E", "Set of precedence edges", "-"),
        ("M", "Set of machines", "-"),
        ("F_j", "DVFS states supported by machine j", "-"),
        ("W_i", "Reference workload of task i", "work units"),
        ("S_j", "Relative processing capacity of machine j", "-"),
        ("f_k", "Relative frequency of DVFS state k", "-"),
        ("V_k", "Relative voltage of DVFS state k", "-"),
        ("beta_i", "CPU-sensitive fraction of task i", "-"),
        ("T_ijk", "Execution time of task i on machine j at state k", "s"),
        ("D_e", "Data volume carried by edge e", "MB"),
        ("B_s", "Bandwidth of network scope s", "Gbps"),
        ("L_s", "Latency of network scope s", "ms"),
        ("q_s", "Queueing multiplier for network scope s", "-"),
        ("h_s", "Hop count for network scope s", "hops"),
        ("epsilon_s", "Energy per bit per hop", "nJ/bit/hop"),
        ("x_ijk", "Task-machine-frequency assignment variable", "binary"),
        ("s_i", "Start time of task i", "s"),
        ("c_i", "Completion time of task i", "s"),
        ("C_max", "Schedule makespan", "s"),
        ("E_total", "Total schedule energy", "J"),
        ("EDP", "Energy-delay product", "J s"),
        ("T_BE", "DPM break-even threshold", "s"),
    ]
    pd.DataFrame(notation, columns=["Symbol", "Definition", "Unit"]).to_csv(
        args.output_dir / "notation_table.csv", index=False
    )

    parameter_rows = []
    power = config["power"]
    parameter_rows.extend(
        [
            ("Peak power", power["peak_power_w"], "W", "Literature-informed scenario parameter"),
            ("Idle power", power["idle_power_w"], "W", "Literature-informed scenario parameter"),
            ("Sleep power", power["sleep_power_w"], "W", "Literature-informed scenario parameter"),
            ("Dynamic-power exponent", power["dynamic_power_alpha"], "-", "Model parameter"),
        ]
    )
    for state in power["frequencies"]:
        parameter_rows.append(
            (
                f"DVFS state {state['id']} frequency",
                state["relative_frequency"],
                "relative",
                "Configured discrete state",
            )
        )
        parameter_rows.append(
            (
                f"DVFS state {state['id']} voltage",
                state["relative_voltage"],
                "relative",
                "Configured discrete state",
            )
        )
    dpm = config["dpm"]
    for label, key, unit in [
        ("DPM break-even threshold", "break_even_threshold_s", "s"),
        ("Sleep-entry delay", "sleep_entry_delay_s", "s"),
        ("Wake-up delay", "wake_up_delay_s", "s"),
        ("Sleep-entry energy", "sleep_entry_energy_j", "J"),
        ("Wake-up energy", "wake_up_energy_j", "J"),
    ]:
        parameter_rows.append((label, dpm[key], unit, "Common to all DPM-enabled schedulers"))
    parameter_rows.append(
        (
            "CPU-sensitive fraction",
            config["execution"]["cpu_sensitive_fraction"],
            "-",
            "Balanced main-workload scenario",
        )
    )
    for scenario, value in config["communication"]["scenario_medians_mb"].items():
        parameter_rows.append(
            (f"{scenario.title()} communication median", value, "MB", "Sensitivity scenario; not trace-measured")
        )
    for scope, values in config["communication"]["scopes"].items():
        parameter_rows.extend(
            [
                (f"{scope} latency", values["latency_ms"], "ms", "Communication model"),
                (f"{scope} bandwidth", values["bandwidth_gbps"], "Gbps", "Communication model"),
                (f"{scope} queue multiplier", values["queue_multiplier"], "-", "Communication model"),
                (f"{scope} hop count", values["hop_count"], "hops", "Communication model"),
                (f"{scope} energy per bit per hop", values["energy_nj_per_bit_hop"], "nJ/bit/hop", "Communication model"),
            ]
        )
    pd.DataFrame(parameter_rows, columns=["Parameter", "Value", "Unit", "Source_or_role"]).to_csv(
        args.output_dir / "simulation_parameter_table.csv", index=False
    )

    complexity = [
        ("DAG validation/topological processing", "O(n+e)", "n tasks, e edges"),
        ("Upward-rank calculation", "O(n+e)", "Assuming average costs are precomputed"),
        ("Priority sorting", "O(n log n)", "Deterministic task order"),
        ("Candidate enumeration", "O(n m f)", "m machines, at most f DVFS states"),
        ("Predecessor readiness without caching", "O(m f e)", "Each incoming edge may be inspected per candidate"),
        ("Overall conservative bound", "O(n log n + e + n m f + m f e)", "No unconditional nm >> n log n claim"),
        ("Overall with cached predecessor readiness", "O(n log n + e + n m f)", "Practical optimised implementation"),
    ]
    pd.DataFrame(complexity, columns=["Operation", "Complexity", "Explanation"]).to_csv(
        args.output_dir / "complexity_analysis_table.csv", index=False
    )

    pseudocode = """Algorithm 1: Communication-Aware Energy-Aware Heterogeneous Scheduling\n\nInput: DAG G=(V,E), jobs and release times, machine set M, DVFS states F_j, communication parameters, power and DPM parameters.\nOutput: Task assignments, frequencies, start and completion times, and machine-state timeline.\n\n1. Validate the DAG, task attributes, machine capacities, and model parameters.\n2. Compute average task execution costs and average edge communication costs.\n3. Compute communication-aware upward ranks recursively.\n4. Initialise machine availability, busy time, current frequency, and power state.\n5. While unscheduled tasks remain:\n   a. Form the ready set whose predecessors have already been assigned.\n   b. Select the ready task with the highest upward rank.\n   c. For every feasible machine and supported frequency:\n      i. Compute predecessor-data readiness and communication cost.\n      ii. Compute earliest machine availability and any DPM wake-up consequence.\n      iii. Compute non-ideal execution time and active energy.\n      iv. Compute projected makespan, schedule energy, and projected EDP.\n      v. Compute secondary locality and load-balance terms.\n   d. Choose the minimum-score candidate using deterministic tie-breaking.\n   e. Commit the assignment and update machine and task state.\n6. Replay the completed assignment through the common event-dependent DPM controller.\n7. Integrate computation, communication, idle, sleep, and actual transition energy exactly once.\n8. Return the schedule and metrics.\n\nThe method is a greedy list heuristic. It minimises projected EDP at each assignment and does not claim global EDP optimality.\n"""
    (args.output_dir / "algorithm_pseudocode.txt").write_text(pseudocode, encoding="utf-8")


if __name__ == "__main__":
    main()
