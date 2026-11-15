from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import copy
import hashlib
import json

import networkx as nx
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Task:
    key: str
    job_id: str
    task_id: str
    release_time_s: float
    base_duration_s: float
    cpu_fraction: float
    memory_fraction: float
    instance_count: int


@dataclass(frozen=True)
class Edge:
    parent: str
    child: str
    data_mb: float = 0.0


@dataclass(frozen=True)
class Machine:
    machine_id: str
    cpu_capacity: float
    memory_capacity: float
    speed_factor: float
    background_cpu_fraction: float
    failure_domain_1: Optional[str] = None
    failure_domain_2: Optional[str] = None
    # Optional per-machine power profile (used by the heterogeneity experiment).
    # When None, the global values in config["power"] are used, so existing
    # results are reproduced exactly.
    machine_class: Optional[str] = None
    idle_power_w: Optional[float] = None
    peak_power_w: Optional[float] = None
    sleep_power_w: Optional[float] = None


@dataclass
class Workload:
    workload_id: str
    tasks: Dict[str, Task]
    edges: List[Edge]
    machines: Dict[str, Machine]

    def predecessors(self) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {key: [] for key in self.tasks}
        for edge in self.edges:
            result[edge.child].append(edge.parent)
        return result

    def successors(self) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {key: [] for key in self.tasks}
        for edge in self.edges:
            result[edge.parent].append(edge.child)
        return result

    def edge_lookup(self) -> Dict[Tuple[str, str], Edge]:
        return {(edge.parent, edge.child): edge for edge in self.edges}

    def graph(self) -> nx.DiGraph:
        graph = nx.DiGraph()
        graph.add_nodes_from(self.tasks)
        graph.add_edges_from((edge.parent, edge.child) for edge in self.edges)
        return graph

    def validate(self) -> None:
        missing = [
            (edge.parent, edge.child)
            for edge in self.edges
            if edge.parent not in self.tasks or edge.child not in self.tasks
        ]
        if missing:
            raise ValueError(f"Edges reference missing tasks. First examples: {missing[:5]}")
        graph = self.graph()
        if not nx.is_directed_acyclic_graph(graph):
            cycle = nx.find_cycle(graph)
            raise ValueError(f"Workload DAG is cyclic. Example cycle: {cycle}")
        if not self.machines:
            raise ValueError("Workload has no machines.")

    def with_edges(self, edges: Sequence[Edge], workload_id: Optional[str] = None) -> "Workload":
        return Workload(
            workload_id=workload_id or self.workload_id,
            tasks=self.tasks,
            edges=list(edges),
            machines=self.machines,
        )

    def with_machine_subset(self, machine_ids: Sequence[str], workload_id: Optional[str] = None) -> "Workload":
        selected = {mid: self.machines[mid] for mid in machine_ids if mid in self.machines}
        if not selected:
            raise ValueError("Requested machine subset is empty.")
        return Workload(
            workload_id=workload_id or self.workload_id,
            tasks=self.tasks,
            edges=self.edges,
            machines=selected,
        )


def load_json(path: Path | str) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def make_task_key(job_id: str, task_id: str) -> str:
    return f"{job_id}::{task_id}"


def _normalise_fraction(value: float, scale_if_large: float = 100.0) -> float:
    if pd.isna(value):
        return 0.0
    value = float(value)
    if value > 1.0:
        value /= scale_if_large
    return float(np.clip(value, 0.0, 1.0))


def load_real_subset(data_dir: Path | str, config: Mapping, workload_id: str = "real_subset") -> Workload:
    """Load the uploaded Alibaba research subset into a common workload object.

    The loader uses the median valid instance runtime when available and otherwise
    falls back to the task-level duration. CPU and memory demands are converted to
    fractions so that the code remains compatible with normalised Alibaba fields.
    """
    data_dir = Path(data_dir)
    required = {
        "jobs": data_dir / "real_jobs.csv",
        "tasks": data_dir / "real_tasks.csv",
        "edges": data_dir / "real_dag_edges.csv",
        "instances": data_dir / "real_instances.csv",
        "capacities": data_dir / "real_machine_capacities.csv",
        "usage": data_dir / "real_machine_usage_summary.csv",
    }
    missing = [str(path) for path in required.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing required files: {missing}")

    jobs = pd.read_csv(required["jobs"])
    tasks = pd.read_csv(required["tasks"])
    edges = pd.read_csv(required["edges"])
    instances = pd.read_csv(required["instances"])
    capacities = pd.read_csv(required["capacities"])
    usage = pd.read_csv(required["usage"])

    use_instance_runtime = bool(config.get("execution", {}).get("use_instance_median_runtime", True))
    runtime_by_task: Dict[Tuple[str, str], float] = {}
    if use_instance_runtime and not instances.empty:
        valid = instances.copy()
        if "is_valid" in valid.columns:
            valid = valid[valid["is_valid"].astype(bool)]
        valid = valid[pd.to_numeric(valid["runtime_seconds"], errors="coerce") > 0]
        grouped = valid.groupby(["job_name", "task_name"], dropna=False)["runtime_seconds"].median()
        runtime_by_task = {(str(job), str(task)): float(value) for (job, task), value in grouped.items()}

    job_release = {
        str(row.job_name): float(row.start_time)
        for row in jobs.itertuples(index=False)
    }

    task_map: Dict[str, Task] = {}
    for row in tasks.itertuples(index=False):
        job_id = str(row.job_name)
        task_id = str(row.task_name)
        key = make_task_key(job_id, task_id)
        fallback = float(max(1e-9, getattr(row, "task_duration_seconds", 1.0)))
        duration = runtime_by_task.get((job_id, task_id), fallback)
        cpu_fraction = _normalise_fraction(getattr(row, "plan_cpu", 0.0), 100.0)
        memory_fraction = _normalise_fraction(getattr(row, "plan_mem", 0.0), 100.0)
        task_map[key] = Task(
            key=key,
            job_id=job_id,
            task_id=task_id,
            release_time_s=job_release.get(job_id, float(getattr(row, "start_time", 0.0))),
            base_duration_s=float(max(duration, 1e-9)),
            cpu_fraction=cpu_fraction,
            memory_fraction=memory_fraction,
            instance_count=int(max(1, getattr(row, "instance_num", 1))),
        )

    edge_list: List[Edge] = []
    for row in edges.itertuples(index=False):
        job_id = str(row.job_name)
        parent = make_task_key(job_id, str(row.parent_task_name))
        child = make_task_key(job_id, str(row.child_task_name))
        if parent in task_map and child in task_map and parent != child:
            edge_list.append(Edge(parent=parent, child=child, data_mb=0.0))

    machine_frame = capacities.merge(usage, on="machine_id", how="left", validate="one_to_one")
    minimum_speed = float(config.get("execution", {}).get("minimum_machine_speed_factor", 0.25))
    machine_map: Dict[str, Machine] = {}
    for row in machine_frame.itertuples(index=False):
        background_cpu = _normalise_fraction(getattr(row, "mean_cpu_util_percent", 0.0), 100.0)
        speed_factor = max(minimum_speed, 1.0 - background_cpu)
        machine_map[str(row.machine_id)] = Machine(
            machine_id=str(row.machine_id),
            cpu_capacity=1.0,
            memory_capacity=1.0,
            speed_factor=float(speed_factor),
            background_cpu_fraction=float(background_cpu),
            failure_domain_1=str(getattr(row, "failure_domain_1", "")),
            failure_domain_2=str(getattr(row, "failure_domain_2", "")),
        )

    workload = Workload(
        workload_id=workload_id,
        tasks=task_map,
        edges=edge_list,
        machines=machine_map,
    )
    workload.validate()
    return workload


def add_communication_scenario(
    workload: Workload,
    scenario: str,
    seed: int,
    config: Mapping,
) -> Workload:
    communication = config["communication"]
    medians = communication["scenario_medians_mb"]
    if scenario not in medians:
        raise KeyError(f"Unknown communication scenario: {scenario}")
    median = float(medians[scenario])
    sigma = float(communication.get("lognormal_sigma", 0.75))
    minimum = float(communication.get("minimum_mb", 0.25))
    maximum = float(communication.get("maximum_mb", 4096.0))
    rng = np.random.default_rng(seed)
    mu = np.log(max(median, 1e-12))
    values = rng.lognormal(mean=mu, sigma=sigma, size=len(workload.edges))
    values = np.clip(values, minimum, maximum)
    new_edges = [replace(edge, data_mb=float(value)) for edge, value in zip(workload.edges, values)]
    return workload.with_edges(new_edges, workload_id=f"{workload.workload_id}_{scenario}_s{seed}")


def select_complete_jobs(
    workload: Workload,
    target_task_count: int,
    seed: int,
    arrival_compression_factor: float = 1.0,
) -> Workload:
    """Select complete jobs until the target task count is reached or exceeded."""
    jobs: Dict[str, List[str]] = {}
    for key, task in workload.tasks.items():
        jobs.setdefault(task.job_id, []).append(key)
    rng = np.random.default_rng(seed)
    job_ids = np.array(sorted(jobs))
    rng.shuffle(job_ids)

    selected_keys: List[str] = []
    selected_jobs: List[str] = []
    for job_id in job_ids:
        selected_jobs.append(str(job_id))
        selected_keys.extend(jobs[str(job_id)])
        if len(selected_keys) >= target_task_count:
            break

    selected_set = set(selected_keys)
    if not selected_set:
        raise ValueError("No jobs were selected for the scalability workload.")

    original_min_release = min(workload.tasks[key].release_time_s for key in selected_set)
    new_tasks: Dict[str, Task] = {}
    for key in selected_set:
        task = workload.tasks[key]
        compressed_release = (task.release_time_s - original_min_release) * arrival_compression_factor
        new_tasks[key] = replace(task, release_time_s=float(compressed_release))

    new_edges = [edge for edge in workload.edges if edge.parent in selected_set and edge.child in selected_set]
    result = Workload(
        workload_id=f"tasks{target_task_count}_seed{seed}_realised{len(new_tasks)}",
        tasks=new_tasks,
        edges=new_edges,
        machines=workload.machines,
    )
    result.validate()
    return result


def bootstrap_synthetic_workload(
    real_workload: Workload,
    number_of_jobs: int,
    seed: int,
    arrival_scale: float = 1.0,
    workload_id: str = "synthetic_bootstrap",
) -> Workload:
    """Create a trace-calibrated synthetic workload by bootstrapping complete real DAGs.

    This intentionally preserves each sampled job's internal DAG structure and task
    characteristics while assigning a new job identifier and resampling inter-arrival
    gaps. It is suitable for reviewer-requested real-versus-synthetic validation.
    """
    rng = np.random.default_rng(seed)
    tasks_by_job: Dict[str, List[Task]] = {}
    for task in real_workload.tasks.values():
        tasks_by_job.setdefault(task.job_id, []).append(task)
    edges_by_job: Dict[str, List[Edge]] = {job_id: [] for job_id in tasks_by_job}
    for edge in real_workload.edges:
        job_id = real_workload.tasks[edge.parent].job_id
        edges_by_job[job_id].append(edge)

    source_jobs = sorted(tasks_by_job)
    source_releases = sorted({min(t.release_time_s for t in tasks_by_job[job]) for job in source_jobs})
    gaps = np.diff(source_releases)
    gaps = gaps[gaps >= 0]
    if len(gaps) == 0:
        gaps = np.array([1.0])

    new_tasks: Dict[str, Task] = {}
    new_edges: List[Edge] = []
    current_release = 0.0
    sampled_jobs = rng.choice(source_jobs, size=number_of_jobs, replace=True)

    for index, source_job in enumerate(sampled_jobs):
        new_job = f"syn_{seed}_{index:05d}"
        if index > 0:
            current_release += float(rng.choice(gaps)) * arrival_scale
        key_mapping: Dict[str, str] = {}
        for source_task in tasks_by_job[source_job]:
            new_key = make_task_key(new_job, source_task.task_id)
            key_mapping[source_task.key] = new_key
            new_tasks[new_key] = replace(
                source_task,
                key=new_key,
                job_id=new_job,
                release_time_s=current_release,
            )
        for edge in edges_by_job[source_job]:
            new_edges.append(
                Edge(
                    parent=key_mapping[edge.parent],
                    child=key_mapping[edge.child],
                    data_mb=edge.data_mb,
                )
            )

    result = Workload(
        workload_id=workload_id,
        tasks=new_tasks,
        edges=new_edges,
        machines=copy.deepcopy(real_workload.machines),
    )
    result.validate()
    return result


def workload_job_metrics(workload: Workload) -> pd.DataFrame:
    """Return reviewer-facing structural and distribution metrics by job."""
    graph = workload.graph()
    rows: List[dict] = []
    tasks_by_job: Dict[str, List[str]] = {}
    for key, task in workload.tasks.items():
        tasks_by_job.setdefault(task.job_id, []).append(key)

    for job_id, keys in tasks_by_job.items():
        subgraph = graph.subgraph(keys).copy()
        task_count = len(keys)
        edge_count = subgraph.number_of_edges()
        if task_count:
            density = edge_count / max(task_count * (task_count - 1) / 2, 1)
        else:
            density = 0.0
        if task_count == 0:
            depth = width = 0
        else:
            generations = list(nx.topological_generations(subgraph)) if edge_count else [set(keys)]
            depth = len(generations)
            width = max(len(generation) for generation in generations)
        task_objects = [workload.tasks[key] for key in keys]
        release = min(task.release_time_s for task in task_objects)
        rows.append(
            {
                "job_id": job_id,
                "release_time_s": release,
                "task_count": task_count,
                "edge_count": edge_count,
                "dag_depth": depth,
                "dag_width": width,
                "edge_density": density,
                "total_reference_runtime_s": sum(task.base_duration_s for task in task_objects),
                "median_task_runtime_s": float(np.median([task.base_duration_s for task in task_objects])),
                "total_instances": sum(task.instance_count for task in task_objects),
                "mean_cpu_fraction": float(np.mean([task.cpu_fraction for task in task_objects])),
                "mean_memory_fraction": float(np.mean([task.memory_fraction for task in task_objects])),
            }
        )
    frame = pd.DataFrame(rows).sort_values("release_time_s").reset_index(drop=True)
    frame["interarrival_time_s"] = frame["release_time_s"].diff().fillna(0.0).clip(lower=0.0)
    return frame


def sha256_file(path: Path | str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def job_size_categories(workload: Workload) -> Dict[str, List[str]]:
    tasks_by_job: Dict[str, List[str]] = {}
    for key, task in workload.tasks.items():
        tasks_by_job.setdefault(task.job_id, []).append(key)
    categories = {"small": [], "medium": [], "large": []}
    for job_id, keys in tasks_by_job.items():
        count = len(keys)
        if count <= 2:
            categories["small"].append(job_id)
        elif count <= 4:
            categories["medium"].append(job_id)
        else:
            categories["large"].append(job_id)
    return categories


def sample_jobs_from_pool(
    workload: Workload,
    candidate_job_ids: Sequence[str],
    target_task_count: int,
    seed: int,
    arrival_scale: float = 1.0,
    workload_id: Optional[str] = None,
) -> Workload:
    tasks_by_job: Dict[str, List[str]] = {}
    for key, task in workload.tasks.items():
        tasks_by_job.setdefault(task.job_id, []).append(key)
    candidates = [job for job in candidate_job_ids if job in tasks_by_job]
    if not candidates:
        raise ValueError("No candidate jobs are available for sampling.")
    rng = np.random.default_rng(seed)
    candidates = list(candidates)
    rng.shuffle(candidates)

    selected_keys: List[str] = []
    for job_id in candidates:
        selected_keys.extend(tasks_by_job[job_id])
        if len(selected_keys) >= target_task_count:
            break
    selected_set = set(selected_keys)
    if not selected_set:
        raise ValueError("Sampling produced an empty workload.")

    minimum_release = min(workload.tasks[key].release_time_s for key in selected_set)
    new_tasks = {
        key: replace(
            workload.tasks[key],
            release_time_s=(workload.tasks[key].release_time_s - minimum_release) * arrival_scale,
        )
        for key in selected_set
    }
    new_edges = [edge for edge in workload.edges if edge.parent in selected_set and edge.child in selected_set]
    result = Workload(
        workload_id=workload_id or f"sample_{target_task_count}_s{seed}",
        tasks=new_tasks,
        edges=new_edges,
        machines=workload.machines,
    )
    result.validate()
    return result
