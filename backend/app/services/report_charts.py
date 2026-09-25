"""Renders verified backend data as static PNG chart images (base64 data URIs) for
embedding in the report — xhtml2pdf can't execute client-side JS charting libraries, so
every report chart is rasterized server-side with matplotlib from real, already-computed
numbers (never re-derived or invented here)."""

import base64
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# A restrained, professional palette — one primary accent (matches the app's own
# --color-primary indigo) plus neutral grays, not a rainbow of colors.
PRIMARY = "#4f46e5"
SECONDARY = "#94a3b8"
PALETTE = ["#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#94a3b8", "#ec4899"]
GRID_COLOR = "#e5e7eb"
TEXT_COLOR = "#1f2937"

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.size": 9,
        "text.color": TEXT_COLOR,
        "axes.edgecolor": "#d1d5db",
        "axes.labelcolor": TEXT_COLOR,
        "xtick.color": "#4b5563",
        "ytick.color": "#4b5563",
        "axes.grid": True,
        "grid.color": GRID_COLOR,
        "grid.linewidth": 0.6,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    }
)


def _to_data_uri(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _truncate(label: str, max_len: int = 18) -> str:
    label = str(label)
    return label if len(label) <= max_len else label[: max_len - 1] + "…"


def bar_chart(labels: list[str], values: list[float], title: str = "", horizontal: bool = True) -> str:
    labels = [_truncate(l) for l in labels]
    fig, ax = plt.subplots(figsize=(6, max(2.2, 0.4 * len(labels) + 0.8)))
    if horizontal:
        ax.barh(labels, values, color=PRIMARY)
        ax.invert_yaxis()
        ax.grid(axis="x")
        ax.grid(axis="y", visible=False)
    else:
        ax.bar(labels, values, color=PRIMARY)
        ax.grid(axis="y")
        ax.grid(axis="x", visible=False)
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    if title:
        ax.set_title(title, fontsize=10, fontweight="bold", loc="left")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    return _to_data_uri(fig)


def donut_chart(labels: list[str], values: list[float], title: str = "") -> str:
    labels = [_truncate(l) for l in labels]
    colors = [PALETTE[i % len(PALETTE)] for i in range(len(labels))]
    fig, ax = plt.subplots(figsize=(4.5, 4))
    wedges, _, autotexts = ax.pie(
        values,
        labels=None,
        autopct=lambda pct: f"{pct:.0f}%" if pct >= 5 else "",
        pctdistance=0.8,
        colors=colors,
        wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 1.5},
        startangle=90,
    )
    for t in autotexts:
        t.set_fontsize(8)
        t.set_color("white")
    ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=8)
    if title:
        ax.set_title(title, fontsize=10, fontweight="bold", loc="left")
    ax.axis("equal")
    return _to_data_uri(fig)


def line_chart(x_labels: list[str], series: list[dict], title: str = "") -> str:
    """series: [{"name": str, "values": list[float]}, ...]"""
    fig, ax = plt.subplots(figsize=(7, 3.2))
    for i, s in enumerate(series):
        ax.plot(x_labels, s["values"], marker="o", markersize=3, linewidth=1.8, label=s["name"], color=PALETTE[i % len(PALETTE)])
    if len(x_labels) > 8:
        step = max(1, len(x_labels) // 8)
        ax.set_xticks(range(0, len(x_labels), step))
        ax.set_xticklabels([x_labels[i] for i in range(0, len(x_labels), step)], rotation=30, ha="right")
    else:
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    if len(series) > 1:
        ax.legend(frameon=False, fontsize=8)
    if title:
        ax.set_title(title, fontsize=10, fontweight="bold", loc="left")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    return _to_data_uri(fig)


def scatter_actual_vs_predicted(actual: list[float], predicted: list[float], title: str = "Actual vs. Predicted") -> str:
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.scatter(actual, predicted, alpha=0.6, s=22, color=PRIMARY, edgecolors="none")
    lo = min(min(actual, default=0), min(predicted, default=0))
    hi = max(max(actual, default=1), max(predicted, default=1))
    ax.plot([lo, hi], [lo, hi], color=SECONDARY, linestyle="--", linewidth=1.2, label="Perfect prediction")
    ax.set_xlabel("Actual")
    ax.set_ylabel("Predicted")
    ax.legend(frameon=False, fontsize=8)
    if title:
        ax.set_title(title, fontsize=10, fontweight="bold", loc="left")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    return _to_data_uri(fig)


def confusion_matrix_chart(labels: list[str], matrix: list[list[int]], title: str = "Confusion Matrix") -> str:
    import numpy as np

    arr = np.array(matrix)
    fig, ax = plt.subplots(figsize=(max(3.5, 0.8 * len(labels) + 1.5), max(3.2, 0.8 * len(labels) + 1.2)))
    im = ax.imshow(arr, cmap="Blues")
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels([_truncate(l, 12) for l in labels], rotation=30, ha="right")
    ax.set_yticklabels([_truncate(l, 12) for l in labels])
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
    vmax = arr.max() if arr.size else 1
    for i in range(arr.shape[0]):
        for j in range(arr.shape[1]):
            color = "white" if arr[i, j] > vmax * 0.5 else TEXT_COLOR
            ax.text(j, i, str(int(arr[i, j])), ha="center", va="center", color=color, fontsize=9)
    if title:
        ax.set_title(title, fontsize=10, fontweight="bold", loc="left")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    return _to_data_uri(fig)


def grouped_bar_chart(categories: list[str], series: list[dict], title: str = "") -> str:
    """series: [{"name": str, "values": list[float]}, ...] — one bar-group per category."""
    import numpy as np

    categories = [_truncate(c) for c in categories]
    n_series = len(series)
    x = np.arange(len(categories))
    width = 0.8 / max(n_series, 1)
    fig, ax = plt.subplots(figsize=(max(5, 1.1 * len(categories) + 1), 3.4))
    for i, s in enumerate(series):
        ax.bar(x + i * width - (0.8 - width) / 2, s["values"], width=width, label=s["name"], color=PALETTE[i % len(PALETTE)])
    ax.set_xticks(x)
    ax.set_xticklabels(categories, rotation=20, ha="right")
    if n_series > 1:
        ax.legend(frameon=False, fontsize=8, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    if title:
        ax.set_title(title, fontsize=10, fontweight="bold", loc="left")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    return _to_data_uri(fig)
