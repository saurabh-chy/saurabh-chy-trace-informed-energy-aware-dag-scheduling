from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .data import Edge, Machine, Task, Workload
from .energy import (
    FrequencyState,
    communication_delay_energy,
    computation_energy_j,
    execution_time_s,
    frequency_states,
    gap_energy_j,
    maximum_frequency,
)


@dataclass
class ScheduleResult:
    scheduler: str
    rows: pd.DataFrame
    candidate_evaluations: int

    def validate_columns(self) -> None:
        required = {
            "task_key",
            "job_id",
            "task_id",
            "machine_id",
            "frequency_id",
            "relative_frequency",
            "relative_voltage",
            "planned_start_s",
            "planned_finish_s",
            "planned_duration_s",
            "scheduler",
            "machine_sequence",
        }
        missing = required - set(self.rows.columns)
        if missing:
            raise ValueError(f"Schedule is missing required columns: {sorted(missing)}")


class BaseScheduler:
    name = "base"

    def __init__(self, config: Mapping):
        self.config = config
        self.candidate_evaluations = 0

    def schedule(self, workload: Workload) -> ScheduleResult:
        raise NotImplementedError

    @staticmethod
    def _ready_tasks(unscheduled: set[str], predecessors: Dict[str, List[str]], scheduled: set[str]) -> List[str]:
        return [task for task in unscheduled if all(parent in scheduled for parent in predecessors[task])]

    def _earliest_start(
        self,
        workload: Workload,
        task_key: str,
        machine_id: str,
        machine_available: Dict[str, float],
        finish_times: Dict[str, float],
        assignments: Dict[str, str],
    ) -> Tuple[float, float, float]:
        task = workload.tasks[task_key]
        predecessors = workload.predecessors()[task_key]
        edge_lookup = workload.edge_lookup()
        predecessor_ready = task.release_time_s
        communication_energy = 0.0
        for parent in predecessors:
            parent_machine = assignments[parent]
            edge = edge_lookup[(parent, task_key)]
            delay, energy, _ = communication_delay_energy(
                edge,
                workload.machines[parent_machine],
                workload.machines[machine_id],
                self.config,
            )
            predecessor_ready = max(predecessor_ready, finish_times[parent] + delay)
            communication_energy += energy
        start = max(task.release_time_s, machine_available[machine_id], predecessor_ready)
        return start, predecessor_ready, communication_energy

    @staticmethod
    def _append_row(
        rows: List[dict],
        task: Task,
        machine_id: str,
        frequency: FrequencyState,
        start: float,
        finish: float,
        scheduler: str,
        machine_sequence: int,
    ) -> None:
        rows.append(
            {
                "task_key": task.key,
                "job_id": task.job_id,
                "task_id": task.task_id,
                "machine_id": machine_id,
                "frequency_id": frequency.state_id,
                "relative_frequency": frequency.relative_frequency,
                "relative_voltage": frequency.relative_voltage,
                "planned_start_s": start,
                "planned_finish_s": finish,
                "planned_duration_s": finish - start,
                "scheduler": scheduler,
                "machine_sequence": machine_sequence,
            }
        )


class FCFSScheduler(BaseScheduler):
    name = "FCFS"

    def schedule(self, workload: Workload) -> ScheduleResult:
        workload.validate()
        max_freq = maximum_frequency(self.config)
        predecessors = workload.predecessors()
        unscheduled = set(workload.tasks)
        scheduled: set[str] = set()
        machine_available = {mid: 0.0 for mid in workload.machines}
        machine_sequences = {mid: 0 for mid in workload.machines}
        finish_times: Dict[str, float] = {}
        assignments: Dict[str, str] = {}
        rows: List[dict] = []

        while unscheduled:
            ready = self._ready_tasks(unscheduled, predecessors, scheduled)
            if not ready:
                raise RuntimeError("No ready tasks; the workload may be cyclic.")
            task_key = min(ready, key=lambda key: (workload.tasks[key].release_time_s, key))
            task = workload.tasks[task_key]
            best = None
            for machine_id, machine in workload.machines.items():
                self.candidate_evaluations += 1
                start, _, _ = self._earliest_start(
                    workload, task_key, machine_id, machine_available, finish_times, assignments
                )
                duration = execution_time_s(task, machine, max_freq, self.config)
                finish = start + duration
                candidate = (finish, start, machine_id, duration)
                if best is None or candidate < best:
                    best = candidate
            assert best is not None
            finish, start, machine_id, duration = best
            machine_sequences[machine_id] += 1
            self._append_row(rows, task, machine_id, max_freq, start, finish, self.name, machine_sequences[machine_id])
            machine_available[machine_id] = finish
            finish_times[task_key] = finish
            assignments[task_key] = machine_id
            scheduled.add(task_key)
            unscheduled.remove(task_key)

        return ScheduleResult(self.name, pd.DataFrame(rows), self.candidate_evaluations)


class RoundRobinScheduler(BaseScheduler):
    name = "RoundRobin"

    def schedule(self, workload: Workload) -> ScheduleResult:
        workload.validate()
        max_freq = maximum_frequency(self.config)
        predecessors = workload.predecessors()
        machine_ids = sorted(workload.machines)
        pointer = 0
        unscheduled = set(workload.tasks)
        scheduled: set[str] = set()
        machine_available = {mid: 0.0 for mid in machine_ids}
        machine_sequences = {mid: 0 for mid in machine_ids}
        finish_times: Dict[str, float] = {}
        assignments: Dict[str, str] = {}
        rows: List[dict] = []

        while unscheduled:
            ready = self._ready_tasks(unscheduled, predecessors, scheduled)
            task_key = min(ready, key=lambda key: (workload.tasks[key].release_time_s, key))
            task = workload.tasks[task_key]
            machine_id = machine_ids[pointer % len(machine_ids)]
            pointer += 1
            machine = workload.machines[machine_id]
            self.candidate_evaluations += 1
            start, _, _ = self._earliest_start(
                workload, task_key, machine_id, machine_available, finish_times, assignments
            )
            duration = execution_time_s(task, machine, max_freq, self.config)
            finish = start + duration
            machine_sequences[machine_id] += 1
            self._append_row(rows, task, machine_id, max_freq, start, finish, self.name, machine_sequences[machine_id])
            machine_available[machine_id] = finish
            finish_times[task_key] = finish
            assignments[task_key] = machine_id
            scheduled.add(task_key)
            unscheduled.remove(task_key)

        return ScheduleResult(self.name, pd.DataFrame(rows), self.candidate_evaluations)


class SJFScheduler(FCFSScheduler):
    name = "SJF"

    def schedule(self, workload: Workload) -> ScheduleResult:
        workload.validate()
        max_freq = maximum_frequency(self.config)
        predecessors = workload.predecessors()
        unscheduled = set(workload.tasks)
        scheduled: set[str] = set()
        machine_available = {mid: 0.0 for mid in workload.machines}
        machine_sequences = {mid: 0 for mid in workload.machines}
        finish_times: Dict[str, float] = {}
        assignments: Dict[str, str] = {}
        rows: List[dict] = []

        while unscheduled:
            ready = self._ready_tasks(unscheduled, predecessors, scheduled)
            task_key = min(
                ready,
                key=lambda key: (
                    workload.tasks[key].base_duration_s,
                    workload.tasks[key].release_time_s,
                    key,
                ),
            )
            task = workload.tasks[task_key]
            best = None
            for machine_id, machine in workload.machines.items():
                self.candidate_evaluations += 1
                start, _, _ = self._earliest_start(
                    workload, task_key, machine_id, machine_available, finish_times, assignments
                )
                duration = execution_time_s(task, machine, max_freq, self.config)
                finish = start + duration
                candidate = (finish, start, machine_id, duration)
                if best is None or candidate < best:
                    best = candidate
            assert best is not None
            finish, start, machine_id, duration = best
            machine_sequences[machine_id] += 1
            self._append_row(rows, task, machine_id, max_freq, start, finish, self.name, machine_sequences[machine_id])
            machine_available[machine_id] = finish
            finish_times[task_key] = finish
            assignments[task_key] = machine_id
            scheduled.add(task_key)
            unscheduled.remove(task_key)

        return ScheduleResult(self.name, pd.DataFrame(rows), self.candidate_evaluations)


class MinMinScheduler(BaseScheduler):
    name = "MinMin"

    def schedule(self, workload: Workload) -> ScheduleResult:
        workload.validate()
        max_freq = maximum_frequency(self.config)
        predecessors = workload.predecessors()
        unscheduled = set(workload.tasks)
        scheduled: set[str] = set()
        machine_available = {mid: 0.0 for mid in workload.machines}
        machine_sequences = {mid: 0 for mid in workload.machines}
        finish_times: Dict[str, float] = {}
        assignments: Dict[str, str] = {}
        rows: List[dict] = []

        while unscheduled:
            ready = self._ready_tasks(unscheduled, predecessors, scheduled)
            overall_best = None
            for task_key in ready:
                task = workload.tasks[task_key]
                task_best = None
                for machine_id, machine in workload.machines.items():
                    self.candidate_evaluations += 1
                    start, _, _ = self._earliest_start(
                        workload, task_key, machine_id, machine_available, finish_times, assignments
                    )
                    duration = execution_time_s(task, machine, max_freq, self.config)
                    finish = start + duration
                    candidate = (finish, start, machine_id, duration)
                    if task_best is None or candidate < task_best:
                        task_best = candidate
                assert task_best is not None
                candidate = (*task_best, task_key)
                if overall_best is None or candidate < overall_best:
                    overall_best = candidate
            assert overall_best is not None
            finish, start, machine_id, duration, task_key = overall_best
            task = workload.tasks[task_key]
            machine_sequences[machine_id] += 1
            self._append_row(rows, task, machine_id, max_freq, start, finish, self.name, machine_sequences[machine_id])
            machine_available[machine_id] = finish
            finish_times[task_key] = finish
            assignments[task_key] = machine_id
            scheduled.add(task_key)
            unscheduled.remove(task_key)

        return ScheduleResult(self.name, pd.DataFrame(rows), self.candidate_evaluations)


def upward_ranks(workload: Workload, config: Mapping) -> Dict[str, float]:
    successors = workload.successors()
    edge_lookup = workload.edge_lookup()
    max_freq = maximum_frequency(config)
    machines = list(workload.machines.values())
    average_duration: Dict[str, float] = {}
    for task_key, task in workload.tasks.items():
        average_duration[task_key] = float(
            np.mean([execution_time_s(task, machine, max_freq, config) for machine in machines])
        )

    remote_machine_pairs = [
        (source, destination)
        for source in machines
        for destination in machines
        if source.machine_id != destination.machine_id
    ]
    ranks: Dict[str, float] = {}

    def rank(task_key: str) -> float:
        if task_key in ranks:
            return ranks[task_key]
        if not successors[task_key]:
            ranks[task_key] = average_duration[task_key]
            return ranks[task_key]
        values = []
        for child in successors[task_key]:
            edge = edge_lookup[(task_key, child)]
            if remote_machine_pairs:
                delays = [
                    communication_delay_energy(edge, source, destination, config)[0]
                    for source, destination in remote_machine_pairs[: min(100, len(remote_machine_pairs))]
                ]
                average_communication = float(np.mean(delays))
            else:
                average_communication = 0.0
            values.append(average_communication + rank(child))
        ranks[task_key] = average_duration[task_key] + max(values)
        return ranks[task_key]

    for task_key in workload.tasks:
        rank(task_key)
    return ranks


class HEFTScheduler(BaseScheduler):
    name = "HEFT"

    def schedule(self, workload: Workload) -> ScheduleResult:
        workload.validate()
        max_freq = maximum_frequency(self.config)
        ranks = upward_ranks(workload, self.config)
        predecessors = workload.predecessors()
        unscheduled = set(workload.tasks)
        scheduled: set[str] = set()
        machine_available = {mid: 0.0 for mid in workload.machines}
        machine_sequences = {mid: 0 for mid in workload.machines}
        finish_times: Dict[str, float] = {}
        assignments: Dict[str, str] = {}
        rows: List[dict] = []

        while unscheduled:
            ready = self._ready_tasks(unscheduled, predecessors, scheduled)
            task_key = max(ready, key=lambda key: (ranks[key], key))
            task = workload.tasks[task_key]
            best = None
            for machine_id, machine in workload.machines.items():
                self.candidate_evaluations += 1
                start, _, _ = self._earliest_start(
                    workload, task_key, machine_id, machine_available, finish_times, assignments
                )
                duration = execution_time_s(task, machine, max_freq, self.config)
                finish = start + duration
                candidate = (finish, start, machine_id, duration)
                if best is None or candidate < best:
                    best = candidate
            assert best is not None
            finish, start, machine_id, duration = best
            machine_sequences[machine_id] += 1
            self._append_row(rows, task, machine_id, max_freq, start, finish, self.name, machine_sequences[machine_id])
            machine_available[machine_id] = finish
            finish_times[task_key] = finish
            assignments[task_key] = machine_id
            scheduled.add(task_key)
            unscheduled.remove(task_key)

        return ScheduleResult(self.name, pd.DataFrame(rows), self.candidate_evaluations)


class EADAGSAdaptedScheduler(BaseScheduler):
    """Reference adaptation of EADAGS for a shared execution and power model.

    The algorithm uses HEFT-style task priority and chooses the earliest-finish
    machine at maximum frequency. It then selects the lowest frequency whose
    finish time remains inside a configured slack envelope. Because this is not an
    exact reproduction of Baskiyar and Abdel-Kader's implementation, results must
    be labelled EADAGS-adapted in the manuscript.
    """

    name = "EADAGS_adapted"

    def schedule(self, workload: Workload) -> ScheduleResult:
        workload.validate()
        ranks = upward_ranks(workload, self.config)
        states = sorted(frequency_states(self.config), key=lambda state: state.relative_frequency)
        max_freq = states[-1]
        slack_fraction = float(self.config["scheduler"].get("eadags_slack_fraction", 0.05))
        predecessors = workload.predecessors()
        unscheduled = set(workload.tasks)
        scheduled: set[str] = set()
        machine_available = {mid: 0.0 for mid in workload.machines}
        machine_sequences = {mid: 0 for mid in workload.machines}
        finish_times: Dict[str, float] = {}
        assignments: Dict[str, str] = {}
        rows: List[dict] = []

        while unscheduled:
            ready = self._ready_tasks(unscheduled, predecessors, scheduled)
            task_key = max(ready, key=lambda key: (ranks[key], key))
            task = workload.tasks[task_key]

            machine_best = None
            for machine_id, machine in workload.machines.items():
                self.candidate_evaluations += 1
                start, _, _ = self._earliest_start(
                    workload, task_key, machine_id, machine_available, finish_times, assignments
                )
                duration = execution_time_s(task, machine, max_freq, self.config)
                finish = start + duration
                candidate = (finish, start, machine_id)
                if machine_best is None or candidate < machine_best:
                    machine_best = candidate
            assert machine_best is not None
            max_finish, _, machine_id = machine_best
            machine = workload.machines[machine_id]
            deadline = max_finish * (1.0 + slack_fraction)

            selected = max_freq
            selected_start = 0.0
            selected_finish = max_finish
            for state in states:
                self.candidate_evaluations += 1
                start, _, _ = self._earliest_start(
                    workload, task_key, machine_id, machine_available, finish_times, assignments
                )
                duration = execution_time_s(task, machine, state, self.config)
                finish = start + duration
                if finish <= deadline:
                    selected = state
                    selected_start = start
                    selected_finish = finish
                    break

            machine_sequences[machine_id] += 1
            self._append_row(
                rows,
                task,
                machine_id,
                selected,
                selected_start,
                selected_finish,
                self.name,
                machine_sequences[machine_id],
            )
            machine_available[machine_id] = selected_finish
            finish_times[task_key] = selected_finish
            assignments[task_key] = machine_id
            scheduled.add(task_key)
            unscheduled.remove(task_key)

        return ScheduleResult(self.name, pd.DataFrame(rows), self.candidate_evaluations)


class ProposedScheduler(BaseScheduler):
    name = "Proposed"

    def schedule(self, workload: Workload) -> ScheduleResult:
        workload.validate()
        ranks = upward_ranks(workload, self.config)
        states = frequency_states(self.config)
        predecessors = workload.predecessors()
        unscheduled = set(workload.tasks)
        scheduled: set[str] = set()
        machine_available = {mid: 0.0 for mid in workload.machines}
        machine_busy = {mid: 0.0 for mid in workload.machines}
        machine_sequences = {mid: 0 for mid in workload.machines}
        finish_times: Dict[str, float] = {}
        assignments: Dict[str, str] = {}
        rows: List[dict] = []
        cumulative_energy = 0.0
        current_makespan = 0.0
        lambda_load = float(self.config["scheduler"].get("proposed_lambda_load", 0.05))
        lambda_comm = float(self.config["scheduler"].get("proposed_lambda_communication", 0.02))

        while unscheduled:
            ready = self._ready_tasks(unscheduled, predecessors, scheduled)
            task_key = max(ready, key=lambda key: (ranks[key], key))
            task = workload.tasks[task_key]
            best = None

            for machine_id, machine in workload.machines.items():
                for state in states:
                    self.candidate_evaluations += 1
                    start, _, communication_energy = self._earliest_start(
                        workload, task_key, machine_id, machine_available, finish_times, assignments
                    )
                    gap = max(start - machine_available[machine_id], 0.0)
                    state_energy, _ = gap_energy_j(gap, dpm_enabled=True, final_tail=False, config=self.config, machine=machine)
                    duration = execution_time_s(task, machine, state, self.config)
                    finish = start + duration
                    active_energy = computation_energy_j(task, machine, state, duration, self.config)
                    projected_energy = cumulative_energy + state_energy + active_energy + communication_energy
                    projected_makespan = max(current_makespan, finish)
                    projected_edp = projected_energy * projected_makespan

                    projected_busy = dict(machine_busy)
                    projected_busy[machine_id] += duration
                    values = np.array(list(projected_busy.values()), dtype=float)
                    load_cv = float(np.std(values) / np.mean(values)) if np.mean(values) > 0 else 0.0
                    remote_predecessors = sum(
                        1
                        for parent in predecessors[task_key]
                        if assignments.get(parent) is not None and assignments[parent] != machine_id
                    )
                    score = projected_edp + lambda_load * load_cv + lambda_comm * remote_predecessors
                    candidate = (
                        score,
                        finish,
                        projected_energy,
                        machine_id,
                        state.state_id,
                        start,
                        duration,
                        state,
                        state_energy,
                        active_energy,
                        communication_energy,
                    )
                    if best is None or candidate[:5] < best[:5]:
                        best = candidate

            assert best is not None
            (
                score,
                finish,
                projected_energy,
                machine_id,
                _,
                start,
                duration,
                state,
                state_energy,
                active_energy,
                communication_energy,
            ) = best
            machine_sequences[machine_id] += 1
            self._append_row(rows, task, machine_id, state, start, finish, self.name, machine_sequences[machine_id])
            machine_available[machine_id] = finish
            machine_busy[machine_id] += duration
            finish_times[task_key] = finish
            assignments[task_key] = machine_id
            cumulative_energy += state_energy + active_energy + communication_energy
            current_makespan = max(current_makespan, finish)
            scheduled.add(task_key)
            unscheduled.remove(task_key)

        return ScheduleResult(self.name, pd.DataFrame(rows), self.candidate_evaluations)


class ProposedV2Scheduler(BaseScheduler):
    """Slack-bounded, DPM-aware marginal-energy list scheduler (Proposed-v2).

    Stage 1 (HEFT-like, O(m) per task): compute a DPM-aware earliest finish time
    on every machine at maximum frequency, including the wake-up delay when the
    machine would have slept through the preceding gap.
    Stage 2 (O(kL) per task): among the k machines with the earliest finish, and
    every DVFS state, keep candidates whose finish time is within
    (1 + epsilon) of the best finish duration bound, and choose the one with the
    lowest *marginal* energy, i.e. the energy added relative to leaving the
    machine in its baseline (sleep) state. This protects the makespan, which
    drives state energy under DPM, while still exploiting machine-level energy
    differences.
    """

    name = "ProposedV2"

    def schedule(self, workload: Workload) -> ScheduleResult:
        from .energy import machine_power_w, processor_power_w

        workload.validate()
        ranks = upward_ranks(workload, self.config)
        states = sorted(frequency_states(self.config), key=lambda s: s.relative_frequency)
        max_freq = states[-1]
        scheduler_cfg = self.config["scheduler"]
        epsilon = float(scheduler_cfg.get("v2_slack_fraction", 0.05))
        top_k = int(scheduler_cfg.get("v2_top_k", 8))
        dpm = self.config["dpm"]
        threshold = float(dpm["break_even_threshold_s"])
        wake_delay = float(dpm.get("wake_up_delay_s", 0.0))
        transition_energy = float(dpm.get("sleep_entry_energy_j", 0.0)) + float(dpm.get("wake_up_energy_j", 0.0))

        predecessors = workload.predecessors()
        unscheduled = set(workload.tasks)
        scheduled: set[str] = set()
        horizon_start = min(task.release_time_s for task in workload.tasks.values())
        machine_available = {mid: horizon_start for mid in workload.machines}
        machine_sequences = {mid: 0 for mid in workload.machines}
        finish_times: Dict[str, float] = {}
        assignments: Dict[str, str] = {}
        rows: List[dict] = []
        powers = {mid: machine_power_w(m, self.config) for mid, m in workload.machines.items()}
        base_ready: Dict[str, float] = {}

        while unscheduled:
            ready = self._ready_tasks(unscheduled, predecessors, scheduled)
            task_key = max(ready, key=lambda key: (ranks[key], key))
            task = workload.tasks[task_key]

            # Stage 1: DPM-aware earliest finish at maximum frequency on every machine.
            stage1 = []
            for machine_id, machine in workload.machines.items():
                self.candidate_evaluations += 1
                start, _, comm_energy = self._earliest_start(
                    workload, task_key, machine_id, machine_available, finish_times, assignments
                )
                gap = max(start - machine_available[machine_id], 0.0)
                effective_start = start + (wake_delay if gap >= threshold else 0.0)
                duration = execution_time_s(task, machine, max_freq, self.config)
                stage1.append((effective_start + duration, machine_id, start, effective_start, gap, comm_energy))
            stage1.sort()
            best_finish, _, _, best_eff_start, _, _ = stage1[0]
            bound = best_finish + epsilon * max(best_finish - best_eff_start, 0.0)

            # Stage 2: marginal-energy choice among the top-k machines and all DVFS states.
            best = None
            for finish_fmax, machine_id, start, effective_start, gap, comm_energy in stage1[:top_k]:
                if finish_fmax > bound:
                    break
                machine = workload.machines[machine_id]
                idle_w, _, sleep_w = powers[machine_id]
                if gap >= threshold:
                    gap_cost = transition_energy
                else:
                    gap_cost = (idle_w - sleep_w) * gap
                for state in states:
                    self.candidate_evaluations += 1
                    duration = execution_time_s(task, machine, state, self.config)
                    finish = effective_start + duration
                    if finish > bound:
                        continue
                    active_w = processor_power_w(task, machine, state, self.config)
                    marginal = (active_w - sleep_w) * duration + gap_cost + comm_energy
                    # Record the post-wake start so planned duration excludes the wake delay
                    # (the common replay adds the wake delay itself).
                    candidate = (marginal, finish, machine_id, state.state_id, effective_start, duration, state)
                    if best is None or candidate[:4] < best[:4]:
                        best = candidate

            assert best is not None, "The earliest-finish machine at maximum frequency always satisfies the bound."
            _, finish, machine_id, _, start, duration, state = best
            base_ready[task_key] = next(item[2] for item in stage1 if item[1] == machine_id)
            machine_sequences[machine_id] += 1
            self._append_row(rows, task, machine_id, state, start, finish, self.name, machine_sequences[machine_id])
            machine_available[machine_id] = finish
            finish_times[task_key] = finish
            assignments[task_key] = machine_id
            scheduled.add(task_key)
            unscheduled.remove(task_key)

        frame = pd.DataFrame(rows)
        if bool(scheduler_cfg.get("v2_slack_reclamation", 1)):
            frame = reclaim_slack(workload, frame, base_ready, self.config)
        return ScheduleResult(self.name, frame, self.candidate_evaluations)


def reclaim_slack(
    workload: Workload,
    frame: pd.DataFrame,
    base_ready: Mapping[str, float],
    config: Mapping,
    policy: str = "energy",
) -> pd.DataFrame:
    """Makespan-preserving DVFS slack reclamation under DPM (Stage 3 of Proposed-v2).

    Tasks are visited in reverse order of planned start. Each task keeps its start
    time and may finish no later than the *base-ready* time of every DAG successor
    (minus the communication delay) and of the next task on its machine, so no
    later task can start later than planned and the makespan cannot grow. Among
    the feasible frequencies, the one with the lowest energy for the task plus
    the following inactive interval (idle, or sleep with transitions) is chosen.

    policy="energy": DPM-aware choice described above (Proposed-v2).
    policy="lowest": the lowest feasible frequency, as in the original EADAGS
    voltage-scaling phase (Baskiyar & Abdel-Kader, 2010), which ignores the
    effect of the following gap on idle/sleep energy.
    """
    from .energy import machine_power_w, processor_power_w

    if frame.empty:
        return frame
    dpm = config["dpm"]
    threshold = float(dpm["break_even_threshold_s"])
    entry_delay = float(dpm.get("sleep_entry_delay_s", 0.0))
    entry_energy = float(dpm.get("sleep_entry_energy_j", 0.0))
    wake_energy = float(dpm.get("wake_up_energy_j", 0.0))
    states = sorted(frequency_states(config), key=lambda s: s.relative_frequency)

    frame = frame.copy()
    start = dict(zip(frame["task_key"], frame["planned_start_s"]))
    finish = dict(zip(frame["task_key"], frame["planned_finish_s"]))
    machine_of = dict(zip(frame["task_key"], frame["machine_id"]))
    makespan_end = max(finish.values())
    successors = workload.successors()
    edge_lookup = workload.edge_lookup()

    next_on_machine: Dict[str, Optional[str]] = {}
    for _, group in frame.sort_values(["machine_id", "machine_sequence"]).groupby("machine_id"):
        ordered = group["task_key"].tolist()
        for current, following in zip(ordered, ordered[1:] + [None]):
            next_on_machine[current] = following

    def gap_cost(gap: float, idle_w: float, sleep_w: float, final: bool) -> float:
        if gap < threshold:
            return idle_w * gap
        return entry_energy + sleep_w * max(gap - entry_delay, 0.0) + (0.0 if final else wake_energy)

    chosen: Dict[str, FrequencyState] = {}
    for task_key in sorted(start, key=lambda key: start[key], reverse=True):
        task = workload.tasks[task_key]
        machine = workload.machines[machine_of[task_key]]
        idle_w, _, sleep_w = machine_power_w(machine, config)
        latest = makespan_end
        for child in successors[task_key]:
            delay, _, _ = communication_delay_energy(
                edge_lookup[(task_key, child)], machine, workload.machines[machine_of[child]], config
            )
            latest = min(latest, base_ready[child] - delay)
        following = next_on_machine.get(task_key)
        boundary = base_ready[following] if following is not None else makespan_end
        latest = min(latest, boundary)

        best = None
        for state in states:
            duration = execution_time_s(task, machine, state, config)
            end = start[task_key] + duration
            if end > latest + 1e-9:
                continue
            energy = processor_power_w(task, machine, state, config) * duration
            energy += gap_cost(max(boundary - end, 0.0), idle_w, sleep_w, following is None)
            if policy == "lowest":
                candidate = (state.relative_frequency, 0.0, state.state_id, state, duration)
            else:
                candidate = (energy, -state.relative_frequency, state.state_id, state, duration)
            if best is None or candidate[:3] < best[:3]:
                best = candidate
        if best is None:
            continue  # keep the stage-2 choice
        state, duration = best[3], best[4]
        chosen[task_key] = state
        finish[task_key] = start[task_key] + duration

    for index, row in frame.iterrows():
        state = chosen.get(row["task_key"])
        if state is None:
            continue
        frame.at[index, "frequency_id"] = state.state_id
        frame.at[index, "relative_frequency"] = state.relative_frequency
        frame.at[index, "relative_voltage"] = state.relative_voltage
        frame.at[index, "planned_finish_s"] = finish[row["task_key"]]
        frame.at[index, "planned_duration_s"] = finish[row["task_key"]] - row["planned_start_s"]
    return frame


class EADAGSFaithfulScheduler(HEFTScheduler):
    """EADAGS following the original two-phase design (Baskiyar & Abdel-Kader, 2010).

    Phase 1: earliest-finish-time list scheduling at maximum frequency (upward-rank
    ordering is used in place of Decisive Path Scheduling, so the placement phase
    is HEFT). Phase 2: each task is slowed to the lowest frequency whose finish,
    plus communication, does not delay any successor's earliest start or the next
    task on its processor. Makespan is therefore not increased by design.
    """

    name = "EADAGS_SR"

    def schedule(self, workload: Workload) -> ScheduleResult:
        result = super().schedule(workload)
        frame = result.rows.copy()
        frame["scheduler"] = self.name
        base_ready = dict(zip(frame["task_key"], frame["planned_start_s"]))
        frame = reclaim_slack(workload, frame, base_ready, self.config, policy="lowest")
        return ScheduleResult(self.name, frame, self.candidate_evaluations)


class HEFTDPMAwareSRScheduler(HEFTScheduler):
    """Ablation: HEFT placement + Proposed-v2's DPM-aware slack reclamation."""

    name = "HEFT_DPMSR"

    def schedule(self, workload: Workload) -> ScheduleResult:
        result = super().schedule(workload)
        frame = result.rows.copy()
        frame["scheduler"] = self.name
        base_ready = dict(zip(frame["task_key"], frame["planned_start_s"]))
        frame = reclaim_slack(workload, frame, base_ready, self.config, policy="energy")
        return ScheduleResult(self.name, frame, self.candidate_evaluations)


def get_scheduler(name: str, config: Mapping) -> BaseScheduler:
    normalised = name.strip().lower().replace("-", "_")
    mapping = {
        "fcfs": FCFSScheduler,
        "roundrobin": RoundRobinScheduler,
        "round_robin": RoundRobinScheduler,
        "rr": RoundRobinScheduler,
        "sjf": SJFScheduler,
        "minmin": MinMinScheduler,
        "min_min": MinMinScheduler,
        "heft": HEFTScheduler,
        "eadags": EADAGSAdaptedScheduler,
        "eadags_adapted": EADAGSAdaptedScheduler,
        "proposed": ProposedScheduler,
        "proposedv2": ProposedV2Scheduler,
        "proposed_v2": ProposedV2Scheduler,
        "eadags_sr": EADAGSFaithfulScheduler,
        "heft_dpmsr": HEFTDPMAwareSRScheduler,
    }
    if normalised not in mapping:
        raise KeyError(f"Unknown scheduler '{name}'. Available: {sorted(mapping)}")
    return mapping[normalised](config)
