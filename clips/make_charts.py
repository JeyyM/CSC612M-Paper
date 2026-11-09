"""Draw the charts for the kernel clock and the CRF comparison.

Reads clips/timing.csv, clips/decisions.csv, and clips/decisions_scenecut.csv.
Writes one PNG per chart into reports.
"""

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

CLIPS = Path(__file__).resolve().parent
REPORTS = CLIPS.parent / "reports"

ORDINARY = "#5c6570"
COMPILER = "#2f5d8c"
HANDWRITTEN = "#1f4e46"
SPREAD = "#5c6570"
SETTLED = "#1f4e46"
TARGET = "#8a3b2c"

def read_rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def comparison_rows(path):
    rows = [row for row in read_rows(path) if row["group"] == "comparison"]
    rows.sort(key=lambda row: int(row["clip"]))
    return rows


def number(row, key):
    text = row[key].strip()
    if not text:
        return None
    return float(text)


def finish(figure, path):
    figure.tight_layout()
    figure.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(figure)
    print(path)


KERNEL_SERIES = (
    ("brightness_ordinary_c", "Ordinary C", ORDINARY),
    ("brightness_compiler_c", "Optimized C", COMPILER),
    ("brightness_avx2", "Handwritten AVX2", HANDWRITTEN),
)


def clips_in_order(rows):
    clips = []
    for row in rows:
        if row["clip"] not in clips:
            clips.append(row["clip"])
    return clips


def kernel_bars(rows, key, ylabel, title, path):
    clips = clips_in_order(rows)
    positions = list(range(len(clips)))
    width = 0.26
    offsets = (-width, 0.0, width)
    peak = 0.0

    figure, axis = plt.subplots(figsize=(11.2, 4.6))
    for offset, (method, label, color) in zip(offsets, KERNEL_SERIES):
        values = [
            float(row[key])
            for clip in clips
            for row in rows
            if row["clip"] == clip and row["method"] == method
        ]
        peak = max(peak, max(values))
        axis.bar(
            [position + offset for position in positions],
            values,
            width=width,
            color=color,
            label=label,
        )
    axis.set_xticks(positions, clips)
    axis.set_xlabel("Clip")
    axis.set_ylabel(ylabel)
    axis.set_title(title)
    axis.set_ylim(0, peak * 1.18)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    place_legend(axis, 3)
    finish(figure, path)


def kernel_time(rows):
    kernel_bars(
        rows,
        "median_s",
        "Median seconds",
        "Time to sum 899 frame pairs",
        REPORTS / "kernel_time.png",
    )


def kernel_speedup(rows):
    kernel_bars(
        rows,
        "speedup_vs_ordinary",
        "Times the ordinary C speed",
        "Speedup against ordinary C",
        REPORTS / "kernel_speedup.png",
    )


def mean_by_method(rows, key):
    totals = {method: [] for method, _label, _color in KERNEL_SERIES}
    for row in rows:
        if row["method"] in totals and row[key].strip():
            totals[row["method"]].append(float(row[key]))
    return {method: sum(values) / len(values) for method, values in totals.items()}


def label_bars(axis, bars, texts):
    for bar, text in zip(bars, texts):
        axis.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            text,
            ha="center",
            va="bottom",
            fontsize=9,
        )


def kernel_summary(rows):
    """One figure for every clip: mean time, and mean speedup against ordinary C."""
    times = mean_by_method(rows, "median_s")
    speedups = mean_by_method(rows, "speedup_vs_ordinary")
    labels = [label for _method, label, _color in KERNEL_SERIES]
    colors = [color for _method, _label, color in KERNEL_SERIES]
    methods = [method for method, _label, _color in KERNEL_SERIES]

    figure, axes = plt.subplots(1, 2, figsize=(10.6, 4.4))

    time_values = [times[method] for method in methods]
    bars = axes[0].bar(labels, time_values, color=colors, width=0.62)
    label_bars(axes[0], bars, [f"{value:.3f} s" for value in time_values])
    axes[0].set_ylabel("Mean of median seconds")
    axes[0].set_title("Time across all 18 clips")
    axes[0].set_ylim(0, max(time_values) * 1.2)

    speedup_values = [speedups[method] for method in methods]
    bars = axes[1].bar(labels, speedup_values, color=colors, width=0.62)
    label_bars(axes[1], bars, [f"{value:.2f}x" for value in speedup_values])
    axes[1].set_ylabel("Mean speedup against ordinary C")
    axes[1].set_title("Speedup across all 18 clips")
    axes[1].set_ylim(0, max(speedup_values) * 1.2)

    for axis in axes:
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
        axis.tick_params(axis="x", labelsize=8)

    finish(figure, REPORTS / "kernel_summary.png")


def place_legend(axis, columns):
    axis.legend(
        frameon=False,
        ncol=columns,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),
    )


def grouped_bars(axis, labels, left, right, left_name, right_name):
    positions = range(len(labels))
    width = 0.38
    axis.bar(
        [p - width / 2 for p in positions],
        left,
        width=width,
        color=SPREAD,
        label=left_name,
    )
    axis.bar(
        [p + width / 2 for p in positions],
        right,
        width=width,
        color=SETTLED,
        label=right_name,
    )
    axis.set_xticks(list(positions), labels)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)


def file_score(rows, filename, title):
    labels = [row["clip"] for row in rows]
    spread = [number(row, "spread_ninth") for row in rows]
    settled = [number(row, "settled_ninth") for row in rows]

    figure, axis = plt.subplots(figsize=(8.4, 4.4))
    grouped_bars(axis, labels, spread, settled, "Regular interval", "Settled")
    axis.axhline(90, color=TARGET, linewidth=1, label="VMAF 90")
    axis.set_ylabel("9th-worst frame, VMAF")
    axis.set_xlabel("Clip")
    axis.set_title(title)
    axis.set_ylim(60, 100)
    place_legend(axis, 3)
    finish(figure, REPORTS / filename)


def file_size(rows):
    labels = [row["clip"] for row in rows]
    spread = [number(row, "spread_bytes") / 1024 for row in rows]
    settled = [number(row, "settled_bytes") / 1024 for row in rows]

    figure, axis = plt.subplots(figsize=(8.4, 4.4))
    grouped_bars(axis, labels, spread, settled, "Regular interval", "Settled")
    axis.set_ylabel("Chosen file, KB")
    axis.set_xlabel("Clip")
    axis.set_title("File size at the CRF each rule chose, scene-cut off")
    place_legend(axis, 2)
    finish(figure, REPORTS / "file_size.png")


def main():
    REPORTS.mkdir(parents=True, exist_ok=True)
    timing = read_rows(CLIPS / "timing.csv")
    kernel_time(timing)
    kernel_speedup(timing)
    kernel_summary(timing)

    off = comparison_rows(CLIPS / "decisions.csv")
    on = comparison_rows(CLIPS / "decisions_scenecut.csv")
    file_score(off, "file_score_scenecut_off.png", "9th-worst frame at the chosen CRF, scene-cut off")
    file_score(on, "file_score_scenecut_on.png", "9th-worst frame at the chosen CRF, scene-cut on")
    file_size(off)


if __name__ == "__main__":
    main()
