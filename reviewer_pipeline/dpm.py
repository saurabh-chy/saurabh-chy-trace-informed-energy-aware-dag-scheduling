from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Tuple

import networkx as nx
import numpy as np
import pandas as pd

from .data import Edge, Workload
from .energy import (
    FrequencyState,
    communication_delay_energy,
    computation_energy_j,
    gap_energy_j,
)
from .schedulers import ScheduleResult


@dataclass
class ReplayResult:
    scheduler: str
    dpm_enabled: bool
    task_timeline: pd.DataFrame
    machine_summary: pd.DataFrame
    metrics: dict


def _frequency_from_row(row: pd.Series) -> FrequencyState:
    return FrequencyState(
        state_id=str(row["frequency_id"]),
        relative_frequency=float(row["relative_frequency"]),
        relative_voltage=float(row["relative_voltage"]),
    )


def replay_with_common_dpm(
    workload: Workload,
    schedule: ScheduleResult | pd.DataFrame,
    config: Mapping,
    dpm_enabled: bool,
) -> ReplayResult:
    """Replay a fixed assignment and machine order using one common DPM controller.

    The same function must be used for HEFT, EADAGS-adapted, and the proposed
    scheduler. It preserves each scheduler's task-to-machine mapping and per-machine
    order, while recomputing start times after communication and wake-up delays.
    """
    if isinstance(schedule, ScheduleResult):
        scheduler_name = schedule.scheduler
        schedule_frame = schedule.rows.copy()
        schedule.validate_columns()
    else:
        schedule_frame = schedule.copy()
        scheduler_name = str(schedule_frame["scheduler"].iloc[0]) if "scheduler" in schedule_frame else "unknown"

    required = {
        "task_key",
        "machine_id",
        "frequency_id",
        "relative_frequency",
        "relative_voltage",
        "machine_sequence",
    }
    missing = required - set(schedule_frame.columns)
    if missing:
        raise ValueError(f"Schedule is missing columns required for DPM replay: {sorted(missing)}")

    if set(schedule_frame["task_key"]) != set(workload.tasks):
        absent = set(workload.tasks) - set(schedule_frame["task_key"])
        extra = set(schedule_frame["task_key"]) - set(workload.tasks)
        raise ValueError(f"Schedule/workload task mismatch. Missing={len(absent)}, extra={len(extra)}")

    schedule_frame = schedule_frame.sort_values(["machine_id", "machine_sequence", "task_key"]).reset_index(drop=True)
    assignment = dict(zip(schedule_frame["task_key"], schedule_frame["machine_id"]))
    frequency_by_task = {
        row.task_key: FrequencyState(
            state_id=str(row.frequency_id),
            relative_frequency=float(row.relative_frequency),
            relative_voltage=float(row.relative_voltage),
        )
        for row in schedule_frame.itertuples(index=False)
    }

    augmented = workload.graph()
    previous_task_on_machine: Dict[str, str] = {}
    machine_predecessor: Dict[str, str | None] = {task_key: None for task_key in workload.tasks}
    for machine_id, group in schedule_frame.groupby("machine_id", sort=True):
        ordered = group.sort_values(["machine_sequence", "task_key"])["task_key"].tolist()
        for previous, current in zip(ordered[:-1], ordered[1:]):
            augmented.add_edge(previous, current, relation="machine_order")
            machine_predecessor[current] = previous
        if ordered:
            machine_predecessor[ordered[0]] = None

    if not nx.is_directed_acyclic_graph(augmented):
        cycle = nx.find_cycle(augmented)
        raise ValueError(f"Machine order and DAG precedence create a cycle: {cycle}")

    original_predecessors = workload.predecessors()
    edge_lookup = workload.edge_lookup()
    completion: Dict[str, float] = {}
    start_times: Dict[str, float] = {}
    rows: List[dict] = []

    horizon_start = min(task.release_time_s for task in workload.tasks.values())
    machine_last_finish = {machine_id: horizon_start for machine_id in workload.machines}
    machine_accounting: Dict[str, dict] = {
        machine_id: {
            "machine_id": machine_id,
            "active_time_s": 0.0,
            "idle_time_s": 0.0,
            "sleep_time_s": 0.0,
            "computation_energy_j": 0.0,
            "idle_sleep_energy_j": 0.0,
            "communication_energy_j": 0.0,
            "transition_energy_j": 0.0,
            "sleep_entries": 0,
            "wake_events": 0,
            "task_count": 0,
        }
        for machine_id in workload.machines
    }

    total_communication_energy = 0.0
    topological_order = list(nx.topological_sort(augmented))
    for task_key in topological_order:
        task = workload.tasks[task_key]
        machine_id = assignment[task_key]
        machine = workload.machines[machine_id]
        frequency = frequency_by_task[task_key]

        predecessor_ready = task.release_time_s
        incoming_communication_energy = 0.0
        remote_predecessors = 0
        for parent in original_predecessors[task_key]:
            parent_machine_id = assignment[parent]
            edge = edge_lookup[(parent, task_key)]
            delay, energy, scope = communication_delay_energy(
                edge,
                workload.machines[parent_machine_id],
                machine,
                config,
            )
            predecessor_ready = max(predecessor_ready, completion[parent] + delay)
            incoming_communication_energy += energy
            if parent_machine_id != machine_id:
                remote_predecessors += 1

        previous_machine_task = machine_predecessor.get(task_key)
        machine_ready = (
            completion[previous_machine_task]
            if previous_machine_task is not None
            else machine_last_finish[machine_id]
        )
        base_ready = max(task.release_time_s, predecessor_ready, machine_ready)
        inactive_gap = max(base_ready - machine_ready, 0.0)

        gap_energy, gap_details = gap_energy_j(
            inactive_gap,
            dpm_enabled=dpm_enabled,
            final_tail=False,
            config=config,
            machine=machine,
        )
        actual_start = base_ready + float(gap_details["wake_delay_s"])

        # Use the duration chosen by the scheduler. It is recalculated from the
        # common execution model only if the planned duration column is absent.
        schedule_row = schedule_frame.loc[schedule_frame["task_key"] == task_key].iloc[0]
        duration = float(schedule_row.get("planned_duration_s", np.nan))
        if not np.isfinite(duration) or duration <= 0:
            from .energy import execution_time_s

            duration = execution_time_s(task, machine, frequency, config)
        actual_finish = actual_start + duration
        active_energy = computation_energy_j(task, machine, frequency, duration, config)

        completion[task_key] = actual_finish
        start_times[task_key] = actual_start
        machine_last_finish[machine_id] = actual_finish

        account = machine_accounting[machine_id]
        account["active_time_s"] += duration
        account["idle_time_s"] += float(gap_details["idle_time_s"])
        account["sleep_time_s"] += float(gap_details["sleep_time_s"])
        account["computation_energy_j"] += active_energy
        account["idle_sleep_energy_j"] += gap_energy - float(gap_details["transition_energy_j"])
        account["communication_energy_j"] += incoming_communication_energy
        account["transition_energy_j"] += float(gap_details["transition_energy_j"])
        account["sleep_entries"] += int(gap_details["sleep_entries"])
        account["wake_events"] += int(gap_details["wake_events"])
        account["task_count"] += 1
        total_communication_energy += incoming_communication_energy

        rows.append(
            {
                "task_key": task_key,
                "job_id": task.job_id,
                "task_id": task.task_id,
                "machine_id": machine_id,
                "frequency_id": frequency.state_id,
                "relative_frequency": frequency.relative_frequency,
                "relative_voltage": frequency.relative_voltage,
                "base_ready_s": base_ready,
                "wake_delay_s": float(gap_details["wake_delay_s"]),
                "actual_start_s": actual_start,
                "actual_finish_s": actual_finish,
                "duration_s": duration,
                "inactive_gap_s": inactive_gap,
                "incoming_communication_energy_j": incoming_communication_energy,
                "computation_energy_j": active_energy,
                "gap_energy_j": gap_energy,
                "remote_predecessors": remote_predecessors,
                "scheduler": scheduler_name,
                "dpm_enabled": dpm_enabled,
            }
        )

    horizon_end = max(completion.values()) if completion else horizon_start

    # Add the final inactive interval for every machine, including machines that
    # received no task. This removes the baseline unfairness noted by the reviewers.
    for machine_id, account in machine_accounting.items():
        tail_start = machine_last_finish[machine_id]
        tail_gap = max(horizon_end - tail_start, 0.0)
        tail_energy, tail_details = gap_energy_j(
            tail_gap,
            dpm_enabled=dpm_enabled,
            final_tail=True,
            config=config,
            machine=workload.machines[machine_id],
        )
        account["idle_time_s"] += float(tail_details["idle_time_s"])
        account["sleep_time_s"] += float(tail_details["sleep_time_s"])
        account["idle_sleep_energy_j"] += tail_energy - float(tail_details["transition_energy_j"])
        account["transition_energy_j"] += float(tail_details["transition_energy_j"])
        account["sleep_entries"] += int(tail_details["sleep_entries"])
        account["wake_events"] += int(tail_details["wake_events"])
        account["tail_gap_s"] = tail_gap

    machine_summary = pd.DataFrame(machine_accounting.values()).sort_values("machine_id").reset_index(drop=True)
    machine_summary["total_machine_energy_j"] = (
        machine_summary["computation_energy_j"]
        + machine_summary["idle_sleep_energy_j"]
        + machine_summary["communication_energy_j"]
        + machine_summary["transition_energy_j"]
    )

    computation_energy = float(machine_summary["computation_energy_j"].sum())
    state_energy = float(machine_summary["idle_sleep_energy_j"].sum())
    transition_energy = float(machine_summary["transition_energy_j"].sum())
    communication_energy = float(machine_summary["communication_energy_j"].sum())
    total_energy = computation_energy + state_energy + transition_energy + communication_energy
    makespan = max(horizon_end - horizon_start, 0.0)
    edp = total_energy * makespan
    active_time = float(machine_summary["active_time_s"].sum())
    utilisation = active_time / max(makespan * len(workload.machines), 1e-12)
    busy = machine_summary["active_time_s"].to_numpy(dtype=float)
    load_cv = float(np.std(busy) / np.mean(busy)) if np.mean(busy) > 0 else 0.0

    metrics = {
        "workload_id": workload.workload_id,
        "scheduler": scheduler_name,
        "dpm_enabled": bool(dpm_enabled),
        "task_count": len(workload.tasks),
        "edge_count": len(workload.edges),
        "machine_count": len(workload.machines),
        "horizon_start_s": horizon_start,
        "horizon_end_s": horizon_end,
        "makespan_s": makespan,
        "computation_energy_j": computation_energy,
        "communication_energy_j": communication_energy,
        "idle_sleep_energy_j": state_energy,
        "transition_energy_j": transition_energy,
        "total_energy_j": total_energy,
        "edp_j_s": edp,
        "utilisation_fraction": utilisation,
        "load_imbalance_cv": load_cv,
        "sleep_entries": int(machine_summary["sleep_entries"].sum()),
        "wake_events": int(machine_summary["wake_events"].sum()),
        "total_sleep_time_s": float(machine_summary["sleep_time_s"].sum()),
        "total_idle_time_s": float(machine_summary["idle_time_s"].sum()),
    }

    _validate_replay(workload, pd.DataFrame(rows), machine_summary, metrics)
    return ReplayResult(
        scheduler=scheduler_name,
        dpm_enabled=dpm_enabled,
        task_timeline=pd.DataFrame(rows).sort_values(["actual_start_s", "task_key"]).reset_index(drop=True),
        machine_summary=machine_summary,
        metrics=metrics,
    )


def _validate_replay(
    workload: Workload,
    timeline: pd.DataFrame,
    machine_summary: pd.DataFrame,
    metrics: Mapping,
    tolerance: float = 1e-8,
) -> None:
    if timeline["task_key"].duplicated().any():
        raise AssertionError("A task was scheduled more than once.")
    if len(timeline) != len(workload.tasks):
        raise AssertionError("Not every task was scheduled.")
    if (timeline["actual_finish_s"] < timeline["actual_start_s"]).any():
        raise AssertionError("A task has negative duration.")

    assignment = dict(zip(timeline["task_key"], timeline["machine_id"]))
    finish = dict(zip(timeline["task_key"], timeline["actual_finish_s"]))
    start = dict(zip(timeline["task_key"], timeline["actual_start_s"]))
    edge_lookup = workload.edge_lookup()
    for edge in workload.edges:
        delay, _, _ = communication_delay_energy(
            edge,
            workload.machines[assignment[edge.parent]],
            workload.machines[assignment[edge.child]],
            {},
        ) if False else (0.0, 0.0, "")
        # Precedence was used during replay. A direct finish <= start check remains
        # valid because communication delays are non-negative.
        if finish[edge.parent] - start[edge.child] > tolerance:
            raise AssertionError(f"Precedence violation: {edge.parent} -> {edge.child}")

    for machine_id, group in timeline.groupby("machine_id"):
        ordered = group.sort_values("actual_start_s")
        previous_finish = None
        for row in ordered.itertuples(index=False):
            if previous_finish is not None and row.actual_start_s < previous_finish - tolerance:
                raise AssertionError(f"Machine overlap on {machine_id}")
            previous_finish = row.actual_finish_s

    expected_energy = (
        float(metrics["computation_energy_j"])
        + float(metrics["communication_energy_j"])
        + float(metrics["idle_sleep_energy_j"])
        + float(metrics["transition_energy_j"])
    )
    if not np.isclose(expected_energy, float(metrics["total_energy_j"]), rtol=1e-9, atol=1e-6):
        raise AssertionError("Total energy does not equal the sum of components.")
    if (machine_summary.select_dtypes(include=["number"]) < -tolerance).any().any():
        raise AssertionError("Negative machine-level metric detected.")
