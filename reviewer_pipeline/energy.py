from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Tuple

from .data import Edge, Machine, Task


@dataclass(frozen=True)
class FrequencyState:
    state_id: str
    relative_frequency: float
    relative_voltage: float


def frequency_states(config: Mapping) -> list[FrequencyState]:
    return [
        FrequencyState(
            state_id=str(item["id"]),
            relative_frequency=float(item["relative_frequency"]),
            relative_voltage=float(item["relative_voltage"]),
        )
        for item in config["power"]["frequencies"]
    ]


def maximum_frequency(config: Mapping) -> FrequencyState:
    return max(frequency_states(config), key=lambda state: state.relative_frequency)


def execution_time_s(task: Task, machine: Machine, frequency: FrequencyState, config: Mapping) -> float:
    beta = float(config["execution"].get("cpu_sensitive_fraction", 0.5))
    beta = min(max(beta, 0.0), 1.0)
    speed = max(machine.speed_factor, 1e-9)
    frequency_factor = max(frequency.relative_frequency, 1e-9)
    return (task.base_duration_s / speed) * ((1.0 - beta) + beta / frequency_factor)


def machine_power_w(machine: Machine | None, config: Mapping) -> Tuple[float, float, float]:
    """Return (idle, peak, sleep) power for a machine, falling back to the global config."""
    power = config["power"]
    idle = float(power["idle_power_w"])
    peak = float(power["peak_power_w"])
    sleep = float(power["sleep_power_w"])
    if machine is not None:
        if machine.idle_power_w is not None:
            idle = float(machine.idle_power_w)
        if machine.peak_power_w is not None:
            peak = float(machine.peak_power_w)
        if machine.sleep_power_w is not None:
            sleep = float(machine.sleep_power_w)
    return idle, peak, sleep


def processor_power_w(
    task: Task,
    machine: Machine,
    frequency: FrequencyState,
    config: Mapping,
) -> float:
    power = config["power"]
    idle, peak, _ = machine_power_w(machine, config)
    alpha = float(power.get("dynamic_power_alpha", 1.4))
    utilisation = min(max(task.cpu_fraction + machine.background_cpu_fraction, 0.0), 1.0)
    dynamic_scale = (
        utilisation**alpha
        * frequency.relative_voltage**2
        * frequency.relative_frequency
    )
    return idle + (peak - idle) * dynamic_scale


def computation_energy_j(
    task: Task,
    machine: Machine,
    frequency: FrequencyState,
    duration_s: float,
    config: Mapping,
) -> float:
    return processor_power_w(task, machine, frequency, config) * max(duration_s, 0.0)


def topology_scope(machine_a: Machine, machine_b: Machine, config: Mapping) -> str:
    if machine_a.machine_id == machine_b.machine_id:
        return "same_machine"
    forced = config["communication"].get("all_remote_scope")
    if forced:
        return str(forced)
    if (
        machine_a.failure_domain_1
        and machine_b.failure_domain_1
        and machine_a.failure_domain_1 == machine_b.failure_domain_1
        and machine_a.failure_domain_2 == machine_b.failure_domain_2
    ):
        return "same_rack"
    if (
        machine_a.failure_domain_1
        and machine_b.failure_domain_1
        and machine_a.failure_domain_1 == machine_b.failure_domain_1
    ):
        return "cross_rack"
    return "cross_cluster"


def communication_delay_energy(
    edge: Edge,
    source_machine: Machine,
    destination_machine: Machine,
    config: Mapping,
) -> Tuple[float, float, str]:
    scope = topology_scope(source_machine, destination_machine, config)
    params = config["communication"]["scopes"][scope]
    if scope == "same_machine":
        return 0.0, 0.0, scope

    latency_s = float(params["latency_ms"]) / 1000.0
    bandwidth_bps = float(params["bandwidth_gbps"]) * 1e9
    data_bits = max(edge.data_mb, 0.0) * 8e6
    queue_multiplier = float(params.get("queue_multiplier", 1.0))
    delay_s = queue_multiplier * (latency_s + data_bits / max(bandwidth_bps, 1e-12))

    fixed_energy = float(params.get("fixed_energy_j", 0.0))
    hops = float(params.get("hop_count", 0.0))
    energy_j_per_bit_hop = float(params.get("energy_nj_per_bit_hop", 0.0)) * 1e-9
    energy_j = fixed_energy + data_bits * hops * energy_j_per_bit_hop
    return delay_s, energy_j, scope


def gap_energy_j(
    gap_s: float,
    dpm_enabled: bool,
    final_tail: bool,
    config: Mapping,
    machine: Machine | None = None,
) -> tuple[float, dict]:
    """Return common idle/sleep energy for an inactive interval.

    For a non-final interval, a wake event is needed before the next task. For the
    final tail, no wake event is required. The model charges transition energy only
    when an actual state change occurs, which corrects the per-task transition
    accounting criticised by the reviewers.
    """
    gap_s = max(float(gap_s), 0.0)
    idle_power, _, sleep_power = machine_power_w(machine, config)
    dpm = config["dpm"]
    threshold = float(dpm["break_even_threshold_s"])

    if not dpm_enabled or gap_s < threshold:
        return idle_power * gap_s, {
            "idle_time_s": gap_s,
            "sleep_time_s": 0.0,
            "transition_energy_j": 0.0,
            "sleep_entries": 0,
            "wake_events": 0,
            "wake_delay_s": 0.0,
        }

    entry_delay = float(dpm.get("sleep_entry_delay_s", 0.0))
    wake_delay = 0.0 if final_tail else float(dpm.get("wake_up_delay_s", 0.0))
    entry_energy = float(dpm.get("sleep_entry_energy_j", 0.0))
    wake_energy = 0.0 if final_tail else float(dpm.get("wake_up_energy_j", 0.0))

    sleep_time = max(gap_s - entry_delay, 0.0)
    energy = entry_energy + sleep_power * sleep_time + wake_energy
    return energy, {
        "idle_time_s": 0.0,
        "sleep_time_s": sleep_time,
        "transition_energy_j": entry_energy + wake_energy,
        "sleep_entries": 1,
        "wake_events": 0 if final_tail else 1,
        "wake_delay_s": wake_delay,
    }
