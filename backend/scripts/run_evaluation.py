"""
PHASE 9 - the evaluation that produces the results table in the report.

Run it:
    cd backend
    .venv\\Scripts\\python.exe scripts/run_evaluation.py --trials 20

Outputs (default backend/eval_out/):
    per_trial.csv        one row per condition x seed x modality
    summary.csv          micro-averaged metrics per condition x modality
    window_sweep.csv     sensitivity of fusion to the match window
    results.md           paste-ready tables for the report
    fig_*.png            figures for the report

What is being measured
----------------------
Three counters are scored against the same ground truth:
    camera  - pose_engine alone
    imu     - detect_imu_reps alone
    fused   - fusion_engine.fuse over both
Metrics: precision, recall, F1 on per-rep detection, plus MAE and MAPE on the
session rep count, which is the number a user actually sees.

Honesty rules this script enforces
----------------------------------
1. Every modality is scored by the SAME function on the SAME ground truth.
2. Fusion gets no information the single-sensor baselines are denied.
3. Simulated and real results are written to separate tables and never
   averaged together.
4. If you have no real sessions yet, the real table prints as empty rather
   than silently falling back to simulation.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.eval.simulator import (  # noqa: E402
    Condition,
    default_conditions,
    run_pose_engine,
    simulate,
)
from app.engines.fusion_engine import (  # noqa: E402
    aggregate,
    detect_imu_reps,
    evaluate,
    fuse,
)

MODALITIES = ("camera", "imu", "fused")


# ==========================================================================
# One trial
# ==========================================================================
def run_trial(
    cond: Condition,
    seed: int,
    match_window_s: float = 0.60,
    low_vis_threshold: float = 0.55,
) -> dict[str, Any]:
    sim = simulate(cond, seed=seed)
    pose = run_pose_engine(sim)
    imu = detect_imu_reps(sim.imu_samples)

    result = fuse(
        pose["reps"], imu,
        visibility_timeline=pose["visibility_timeline"],
        match_window_s=match_window_s,
        low_vis_threshold=low_vis_threshold,
    )
    fused_times = [r.t for r in result.reps if r.accepted]

    metrics = {
        "camera": evaluate(pose["times"], sim.gt_times, allow_offset=True),
        "imu": evaluate([r.t for r in imu], sim.gt_times, allow_offset=True),
        "fused": evaluate(fused_times, sim.gt_times, allow_offset=True),
    }
    return {
        "condition": cond.name,
        "seed": seed,
        "gt": len(sim.gt_times),
        "metrics": metrics,
        "offset_true": -cond.clock_offset_s,
        "offset_est": result.offset_s,
        "matched": result.matched,
        "camera_only": result.camera_only,
        "imu_only": result.imu_only,
    }


# ==========================================================================
# Sweeps
# ==========================================================================
def run_all(conditions: list[Condition], trials: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cond in conditions:
        for seed in range(trials):
            out.append(run_trial(cond, seed))
        print(f"  {cond.name:24s} {trials} trials done")
    return out


def window_sweep(cond: Condition, trials: int,
                 windows: tuple[float, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for w in windows:
        per = [run_trial(cond, s, match_window_s=w) for s in range(trials)]
        agg = aggregate(t["metrics"]["fused"] for t in per)
        agg["match_window_s"] = w
        agg["condition"] = cond.name
        rows.append(agg)
        print(f"  window {w:.2f}s -> F1 {agg['f1']:.3f}  MAE {agg['mae_count']:.2f}")
    return rows


# ==========================================================================
# Real recorded sessions
# ==========================================================================
def load_real_sessions(folder: Path) -> list[dict[str, Any]]:
    """
    Each file is one recorded session:

        {
          "name": "s01_bicep_partial_occlusion",
          "exercise": "bicep_curl",
          "ground_truth_times": [4.1, 6.6, ...],   # or "ground_truth_count": 12
          "cam_frames":  [{"t":..,"lm":[[x,y,z,v]x33],"wlm":[[x,y,z]x33]}, ...],
          "imu_samples": [{"t":..,"ax":..,"ay":..,"az":..,"gx":..,"gy":..,"gz":..}]
        }

    Export them with scripts/export_session.py once you have recorded real
    workouts through the web app.
    """
    if not folder.exists():
        return []
    out = []
    for f in sorted(folder.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            print(f"  ! skipping {f.name}: {exc}")
    return out


def run_real(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.engines.pose_engine import PoseEngine

    rows: list[dict[str, Any]] = []
    for s in sessions:
        eng = PoseEngine(s.get("exercise", "bicep_curl"))
        for fr in s.get("cam_frames", []):
            eng.update(fr["t"], fr["lm"], fr.get("wlm"))
        cam_reps = [r.to_dict() for r in eng.reps]
        imu = detect_imu_reps(s.get("imu_samples", []))
        vis = [(fr["t"], fr["vis"]) for fr in eng.frames]
        res = fuse(cam_reps, imu, visibility_timeline=vis)
        fused_times = [r.t for r in res.reps if r.accepted]

        gt_times = s.get("ground_truth_times")
        if gt_times:
            metrics = {
                "camera": evaluate([r.end_ts for r in eng.reps], gt_times, allow_offset=True),
                "imu": evaluate([r.t for r in imu], gt_times, allow_offset=True),
                "fused": evaluate(fused_times, gt_times, allow_offset=True),
            }
            gt_n = len(gt_times)
        else:
            # Count-only ground truth: detection metrics are undefined, so we
            # report count error and leave precision/recall blank rather than
            # inventing timings.
            gt_n = int(s.get("ground_truth_count", 0))
            metrics = {
                m: {"n_gt": gt_n, "n_pred": n, "tp": 0, "fp": 0, "fn": 0,
                    "precision": 0.0, "recall": 0.0, "f1": 0.0,
                    "count_error": n - gt_n, "abs_count_error": abs(n - gt_n),
                    "count_accuracy": (1 - abs(n - gt_n) / gt_n) if gt_n else 0.0,
                    "mean_timing_error_ms": 0.0}
                for m, n in (("camera", len(eng.reps)), ("imu", len(imu)),
                             ("fused", len(fused_times)))
            }
        rows.append({
            "condition": s.get("name", "real"),
            "seed": 0,
            "gt": gt_n,
            "metrics": metrics,
            "offset_true": None,
            "offset_est": res.offset_s,
            "matched": res.matched,
            "camera_only": res.camera_only,
            "imu_only": res.imu_only,
            "timed_gt": bool(gt_times),
        })
    return rows


# ==========================================================================
# Reporting
# ==========================================================================
def write_per_trial(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["condition", "seed", "modality", "n_gt", "n_pred", "tp", "fp", "fn",
                    "precision", "recall", "f1", "count_error", "abs_count_error",
                    "mean_timing_error_ms", "offset_true", "offset_est"])
        for r in rows:
            for m in MODALITIES:
                x = r["metrics"][m]
                w.writerow([r["condition"], r["seed"], m, x["n_gt"], x["n_pred"],
                            x["tp"], x["fp"], x["fn"], x["precision"], x["recall"],
                            x["f1"], x["count_error"], x["abs_count_error"],
                            x["mean_timing_error_ms"], r["offset_true"], r["offset_est"]])


def summarise(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, float]]]:
    conds: list[str] = []
    for r in rows:
        if r["condition"] not in conds:
            conds.append(r["condition"])
    out: dict[str, dict[str, dict[str, float]]] = {}
    for c in conds:
        sub = [r for r in rows if r["condition"] == c]
        out[c] = {m: aggregate(r["metrics"][m] for r in sub) for m in MODALITIES}
    return out


def write_summary(summary: dict, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["condition", "modality", "sessions", "precision", "recall", "f1",
                    "mae_count", "mape_count_pct", "mean_timing_error_ms",
                    "tp", "fp", "fn"])
        for c, mods in summary.items():
            for m, a in mods.items():
                w.writerow([c, m, a["sessions"], a["precision"], a["recall"], a["f1"],
                            a["mae_count"], a["mape_count_pct"],
                            a["mean_timing_error_ms"], a["tp"], a["fp"], a["fn"]])


def md_table(summary: dict, title: str) -> str:
    lines = [f"### {title}", "",
             "| Condition | Modality | Precision | Recall | F1 | MAE (reps) | MAPE % | FP | FN |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for c, mods in summary.items():
        for m in MODALITIES:
            a = mods[m]
            bold = "**" if m == "fused" else ""
            lines.append(
                f"| {c} | {bold}{m}{bold} | {a['precision']:.3f} | {a['recall']:.3f} | "
                f"{bold}{a['f1']:.3f}{bold} | {a['mae_count']:.2f} | "
                f"{a['mape_count_pct']:.1f} | {a['fp']} | {a['fn']} |"
            )
    return "\n".join(lines)


def headline(summary: dict) -> str:
    """
    The claim paragraph - computed, never typed by hand.

    Note what is deliberately NOT claimed. Averaging F1 across conditions
    flatters nobody honestly: in four of six cells one sensor is already
    near-perfect, so the mean gain looks small. The defensible argument for
    fusion is about the worst case and about not knowing in advance which
    sensor is the one that will fail - so those are the numbers reported.
    """
    def col(m: str, key: str) -> list[float]:
        return [v[m][key] for v in summary.values()]

    cam_f1, imu_f1, fus_f1 = col("camera", "f1"), col("imu", "f1"), col("fused", "f1")
    cam_mae, imu_mae, fus_mae = (col("camera", "mae_count"), col("imu", "mae_count"),
                                 col("fused", "mae_count"))

    cam_best = sum(1 for c, i in zip(cam_f1, imu_f1) if c > i + 1e-9)
    imu_best = sum(1 for c, i in zip(cam_f1, imu_f1) if i > c + 1e-9)
    oracle = statistics.mean(max(c, i) for c, i in zip(cam_f1, imu_f1))
    never_worse = sum(1 for f, c, i in zip(fus_f1, cam_f1, imu_f1)
                      if f >= max(c, i) - 1e-9)

    return "\n\n".join([
        f"**Mean F1** over {len(summary)} conditions: camera "
        f"{statistics.mean(cam_f1):.3f}, IMU {statistics.mean(imu_f1):.3f}, "
        f"fused {statistics.mean(fus_f1):.3f}.",

        f"**Worst-case F1** (the number that decides whether a user trusts the "
        f"rep count): camera {min(cam_f1):.3f}, IMU {min(imu_f1):.3f}, fused "
        f"{min(fus_f1):.3f}. Fusion raises the floor by "
        f"{min(fus_f1) - max(min(cam_f1), min(imu_f1)):+.3f} F1 over the better "
        f"single sensor's worst case.",

        f"**Mean absolute count error** per session: camera "
        f"{statistics.mean(cam_mae):.2f}, IMU {statistics.mean(imu_mae):.2f}, "
        f"fused {statistics.mean(fus_mae):.2f} reps.",

        f"**Which sensor wins changes with the condition**: the camera is better "
        f"in {cam_best} of {len(summary)} conditions and the IMU in {imu_best}. "
        f"A system committed to either one in advance cannot reach the "
        f"{oracle:.3f} mean F1 of an oracle that always picks the better sensor; "
        f"fusion reaches {statistics.mean(fus_f1):.3f} without being told which "
        f"is which, and is no worse than the better sensor in {never_worse} of "
        f"{len(summary)} conditions.",
    ])


def offset_accuracy(rows: list[dict[str, Any]]) -> str:
    errs = [abs(r["offset_est"] - r["offset_true"])
            for r in rows if r.get("offset_true") is not None]
    if not errs:
        return "Clock offset recovery: not applicable."
    return (f"Clock offset recovery: mean absolute error "
            f"{1000 * statistics.mean(errs):.0f} ms over {len(errs)} trials "
            f"(median {1000 * statistics.median(errs):.0f} ms).")


# ==========================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="Camera+IMU fusion evaluation")
    ap.add_argument("--trials", type=int, default=20,
                    help="seeds per simulated condition (default 20)")
    ap.add_argument("--out", type=Path, default=BACKEND / "eval_out")
    ap.add_argument("--real-dir", type=Path, default=BACKEND / "data" / "sessions")
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--skip-sweep", action="store_true")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    conditions = default_conditions()

    print(f"Simulated evaluation: {len(conditions)} conditions x {args.trials} trials")
    sim_rows = run_all(conditions, args.trials)
    sim_summary = summarise(sim_rows)

    write_per_trial(sim_rows, args.out / "per_trial.csv")
    write_summary(sim_summary, args.out / "summary.csv")

    sweep_rows: list[dict[str, Any]] = []
    if not args.skip_sweep:
        print("Match-window sensitivity sweep (condition C6):")
        sweep_rows = window_sweep(
            conditions[-1], max(5, args.trials // 2),
            (0.15, 0.25, 0.40, 0.60, 0.80, 1.00, 1.40),
        )
        with (args.out / "window_sweep.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["match_window_s", "precision", "recall", "f1", "mae_count"])
            for r in sweep_rows:
                w.writerow([r["match_window_s"], r["precision"], r["recall"],
                            r["f1"], r["mae_count"]])

    real_sessions = load_real_sessions(args.real_dir)
    real_rows = run_real(real_sessions) if real_sessions else []
    real_summary = summarise(real_rows) if real_rows else {}
    if real_rows:
        write_per_trial(real_rows, args.out / "per_trial_real.csv")
        write_summary(real_summary, args.out / "summary_real.csv")

    # ---- markdown report ----
    parts = [
        "# Fusion evaluation results",
        "",
        f"Generated by `scripts/run_evaluation.py --trials {args.trials}`.",
        "",
        "## Headline",
        "",
        headline(sim_summary),
        "",
        offset_accuracy(sim_rows),
        "",
        md_table(sim_summary, f"Table 1 - Controlled simulation ({args.trials} seeds per cell)"),
        "",
        "Conditions: C1 clean; C2 40% of reps occluded; C3 camera sees rest-position "
        "fidgets; C4 IMU strap slips on 35% of reps; C5 equipment knocked 4 times; "
        "C6 all of the above at realistic severity.",
        "",
    ]
    if sweep_rows:
        parts += [
            "### Table 2 - Match window sensitivity (condition C6)",
            "",
            "| Window (s) | Precision | Recall | F1 | MAE (reps) |",
            "|---:|---:|---:|---:|---:|",
            *[f"| {r['match_window_s']:.2f} | {r['precision']:.3f} | {r['recall']:.3f} "
              f"| {r['f1']:.3f} | {r['mae_count']:.2f} |" for r in sweep_rows],
            "",
        ]
    if real_summary:
        parts += [md_table(real_summary,
                           f"Table 3 - Real recorded sessions (n={len(real_rows)})"), ""]
    else:
        parts += [
            "### Table 3 - Real recorded sessions",
            "",
            "**EMPTY - no recorded sessions found in `backend/data/sessions/`.**",
            "",
            "The simulation above establishes *why* and *when* fusion helps. It is not",
            "a validation on human data and must not be reported as one. Record real",
            "sessions through the web app, export them with `scripts/export_session.py`,",
            "and re-run this script before submitting.",
            "",
        ]
    (args.out / "results.md").write_text("\n".join(parts), encoding="utf-8")

    if not args.no_figures:
        try:
            from app.eval.figures import make_all

            made = make_all(sim_summary, sim_rows, sweep_rows, args.out)
            print(f"Figures: {', '.join(f.name for f in made)}")
        except ImportError as exc:
            print(f"Figures skipped ({exc}). Install matplotlib: "
                  f"pip install -r requirements-dev.txt")

    print()
    print(headline(sim_summary))
    print(offset_accuracy(sim_rows))
    print(f"\nWrote results to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
