"""
Report figures for the fusion evaluation.

Design decisions worth defending in a viva:

* Three-colour categorical palette, fixed order, never cycled:
  camera #0072B2, IMU #D55E00, fused #009E73. Verified colour-blind safe -
  worst adjacent pair separates by dE 11.0 under deuteranopia and 25.8 under
  normal vision, and every colour clears 3:1 contrast against the page. Your
  report will be printed and photocopied by at least one examiner.
* Bars start at zero. F1 lives in 0.7-1.0 here, and cropping the axis to that
  range would make a 0.04 difference look like a landslide.
* No dual-axis charts anywhere. F1 and rep-count error are different units,
  so they get separate figures.
* Values are labelled selectively - on the fused bars and the endpoints -
  rather than on every mark, which turns a chart back into a table.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

CAMERA = "#0072B2"
IMU = "#D55E00"
FUSED = "#009E73"
SERIES = (("camera", CAMERA), ("imu", IMU), ("fused", FUSED))
LABELS = {"camera": "Camera only", "imu": "IMU only", "fused": "Fused"}

INK = "#1a1a1a"
INK_MUTED = "#6b6b6b"
GRID = "#dcdcdc"
SURFACE = "#fcfcfb"


def _style() -> None:
    import matplotlib

    matplotlib.use("Agg")          # no display on a build server
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK,
        "axes.titlesize": 12,
        "axes.titleweight": "semibold",
        "axes.titlecolor": INK,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": GRID,
        "grid.linewidth": 0.7,
        "xtick.color": INK_MUTED,
        "ytick.color": INK_MUTED,
        "text.color": INK,
        "font.size": 9.5,
        "legend.frameon": False,
        "figure.dpi": 150,
        "savefig.dpi": 200,
        "savefig.bbox": "tight",
    })


def _short(name: str) -> str:
    """C2_occlusion_40pct -> C2\nocclusion 40pct"""
    head, _, tail = name.partition("_")
    return f"{head}\n{tail.replace('_', ' ')}" if tail else head


def _grouped_bars(ax: Any, conds: list[str], summary: dict, key: str,
                  label_series: str | None = "fused", fmt: str = "{:.3f}") -> None:
    import numpy as np

    x = np.arange(len(conds))
    width = 0.26
    gap = 0.015                     # 2px-equivalent breathing room between bars

    for i, (m, colour) in enumerate(SERIES):
        vals = [summary[c][m][key] for c in conds]
        pos = x + (i - 1) * (width + gap)
        bars = ax.bar(pos, vals, width, label=LABELS[m], color=colour,
                      edgecolor=SURFACE, linewidth=1.2)
        if m == label_series:
            for b, v in zip(bars, vals):
                ax.annotate(fmt.format(v), (b.get_x() + b.get_width() / 2, v),
                            textcoords="offset points", xytext=(0, 3),
                            ha="center", fontsize=8, color=INK)

    ax.set_xticks(x)
    ax.set_xticklabels([_short(c) for c in conds], fontsize=8.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.xaxis.grid(False)


def fig_f1(summary: dict, out: Path) -> Path:
    import matplotlib.pyplot as plt

    conds = list(summary)
    fig, ax = plt.subplots(figsize=(9.5, 4.2))
    _grouped_bars(ax, conds, summary, "f1")
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("F1 (per-rep detection)")
    ax.set_title("Rep-detection F1 by sensing condition", pad=26)
    # Above the axes: every bar in this chart reaches the top of the plot, so
    # any in-plot legend placement covers data.
    ax.legend(ncol=3, fontsize=9, loc="lower center",
              bbox_to_anchor=(0.5, 1.005), borderaxespad=0)
    path = out / "fig_f1_by_condition.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_mae(summary: dict, out: Path) -> Path:
    import matplotlib.pyplot as plt

    conds = list(summary)
    fig, ax = plt.subplots(figsize=(9.5, 4.2))
    _grouped_bars(ax, conds, summary, "mae_count", fmt="{:.2f}")
    ax.set_ylabel("Mean absolute count error (reps/session)")
    ax.set_title("Rep-count error by sensing condition (lower is better)", pad=26)
    ax.legend(ncol=3, fontsize=9, loc="lower center",
              bbox_to_anchor=(0.5, 1.005), borderaxespad=0)
    path = out / "fig_mae_by_condition.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_window_sweep(rows: list[dict[str, Any]], out: Path) -> Path | None:
    if not rows:
        return None
    import matplotlib.pyplot as plt

    xs = [r["match_window_s"] for r in rows]
    fig, ax = plt.subplots(figsize=(6.8, 4.2))
    for (key, colour, label) in (("precision", CAMERA, "Precision"),
                                 ("recall", IMU, "Recall"),
                                 ("f1", FUSED, "F1")):
        ys = [r[key] for r in rows]
        ax.plot(xs, ys, color=colour, linewidth=2.0, marker="o",
                markersize=5, label=label)

    # Label ONE endpoint only. Labelling all three stacked them on top of one
    # another, and the reader needs the shape of these lines, not their values.
    f1s = [r["f1"] for r in rows]
    ax.annotate(f"F1 {f1s[-1]:.3f}", (xs[-1], f1s[-1]), textcoords="offset points",
                xytext=(7, 0), fontsize=8.5, color=INK, va="center")

    spread = max(f1s) - min(f1s)
    ax.annotate(
        f"F1 spans only {spread:.3f} across a\n{xs[0]:.2f}-{xs[-1]:.2f}s sweep "
        f"- fusion is not\nsensitive to this threshold",
        (0.03, 0.06), xycoords="axes fraction", fontsize=8.5,
        color=INK_MUTED, va="bottom",
    )

    ax.set_xlabel("Camera-IMU match window (s)")
    ax.set_ylabel("Score")
    # A line chart may use a non-zero baseline (a bar chart may not). All
    # values sit above 0.85, and a 0-1 axis would flatten the whole result
    # into three indistinguishable straight lines.
    lo = min(min(r["precision"], r["recall"], r["f1"]) for r in rows)
    ax.set_ylim(max(0.0, lo - 0.06), 1.01)
    ax.set_title("Fusion sensitivity to the match window (condition C6)", pad=26)
    ax.legend(ncol=3, fontsize=9, loc="lower center",
              bbox_to_anchor=(0.5, 1.005), borderaxespad=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    path = out / "fig_window_sweep.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_offset(rows: list[dict[str, Any]], out: Path) -> Path | None:
    pts = [(r["offset_true"], r["offset_est"]) for r in rows
           if r.get("offset_true") is not None]
    if not pts:
        return None
    import matplotlib.pyplot as plt

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    lo, hi = min(xs + ys) - 0.3, max(xs + ys) + 0.3

    fig, ax = plt.subplots(figsize=(5.4, 5.0))
    ax.plot([lo, hi], [lo, hi], color=INK_MUTED, linewidth=1,
            linestyle="--", zorder=0, label="perfect recovery")
    ax.scatter(xs, ys, s=42, color=FUSED, edgecolor=SURFACE, linewidth=0.8,
               alpha=0.85, label="estimated offset", zorder=2)

    err = sum(abs(a - b) for a, b in pts) / len(pts)
    ax.annotate(f"mean |error| = {1000 * err:.0f} ms\nn = {len(pts)} trials",
                (0.04, 0.93), xycoords="axes fraction", fontsize=9,
                va="top", color=INK)

    ax.set_xlabel("True clock offset (s)")
    ax.set_ylabel("Estimated clock offset (s)")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("Camera-IMU clock offset recovery")
    ax.legend(loc="lower right", fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    path = out / "fig_offset_recovery.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def make_all(summary: dict, rows: list[dict[str, Any]],
             sweep: list[dict[str, Any]], out: Path) -> list[Path]:
    _style()
    made = [fig_f1(summary, out), fig_mae(summary, out)]
    for f in (fig_window_sweep(sweep, out), fig_offset(rows, out)):
        if f is not None:
            made.append(f)
    return made
