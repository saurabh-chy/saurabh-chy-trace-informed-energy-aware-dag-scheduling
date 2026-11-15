from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import sys

import psutil


def main() -> None:
    parser = argparse.ArgumentParser(description="Record the exact software and hardware environment used for experiments.")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    packages = {}
    for name in ["pandas", "numpy", "scipy", "matplotlib", "networkx", "psutil"]:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None

    report = {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "operating_system": platform.system(),
        "operating_system_release": platform.release(),
        "machine_architecture": platform.machine(),
        "processor": platform.processor(),
        "logical_cpu_count": psutil.cpu_count(logical=True),
        "physical_cpu_count": psutil.cpu_count(logical=False),
        "total_ram_bytes": psutil.virtual_memory().total,
        "process_id": os.getpid(),
        "packages": packages,
        "timing_method": "time.perf_counter",
        "memory_method": "tracemalloc peak traced Python allocation",
        "parallelism": "Schedulers run sequentially unless the user explicitly parallelises the driver.",
    }
    (args.output_dir / "environment_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    text = "\n".join(f"{key}: {value}" for key, value in report.items() if key != "packages")
    text += "\npackages:\n" + "\n".join(f"  {name}: {version}" for name, version in packages.items())
    (args.output_dir / "environment_report.txt").write_text(text, encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
