from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the missing end-to-end research workflow figure.")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    labels = [
        "Alibaba trace\nacquisition",
        "Integrity checks\nand chunked extraction",
        "Cleaning and\nDAG reconstruction",
        "Trace\ncharacterisation",
        "Real replay and\nsynthetic generation",
        "Workload\nvalidation",
        "Common simulation\nand power engine",
        "Scheduler and\ncommon-DPM runs",
        "Metrics, ablation\nand scalability",
        "Paired statistics and\nreproducibility audit",
    ]

    fig, ax = plt.subplots(figsize=(13.5, 5.8))
    ax.set_xlim(0, 5)
    ax.set_ylim(0, 2)
    ax.axis("off")

    positions = []
    for index in range(5):
        positions.append((index + 0.5, 1.45))
    for index in range(5):
        positions.append((4.5 - index, 0.55))

    boxes = []
    for (x, y), label in zip(positions, labels):
        box = FancyBboxPatch(
            (x - 0.39, y - 0.22),
            0.78,
            0.44,
            boxstyle="round,pad=0.04",
            linewidth=1.2,
            facecolor="white",
        )
        ax.add_patch(box)
        ax.text(x, y, label, ha="center", va="center", fontsize=9)
        boxes.append(box)

    for first, second in zip(positions[:-1], positions[1:]):
        x1, y1 = first
        x2, y2 = second
        arrow = FancyArrowPatch(
            (x1 + (0.41 if x2 > x1 else -0.41 if x2 < x1 else 0), y1),
            (x2 - (0.41 if x2 > x1 else -0.41 if x2 < x1 else 0), y2),
            arrowstyle="-|>",
            mutation_scale=12,
            linewidth=1.1,
            connectionstyle="arc3,rad=0.0" if y1 == y2 else "arc3,rad=0.15",
        )
        ax.add_patch(arrow)

    ax.set_title("Trace-informed scheduling research and experimental workflow", fontsize=13, pad=12)
    fig.tight_layout()
    fig.savefig(args.output_dir / "research_workflow.png", dpi=300, bbox_inches="tight")
    fig.savefig(args.output_dir / "research_workflow.pdf", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
