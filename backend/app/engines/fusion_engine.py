"""
fusion_engine - the academic contribution of this project.

Problem
-------
A webcam rep counter fails in predictable ways: occlusion (the bar, a bench,
another person), motion blur, and landmark jitter that fabricates reps. An
IMU bolted to the equipment fails in a *different* set of ways: it cannot tell
a rep from someone bumping the dumbbell, it has no idea about form, and it
drifts. The two failure sets are largely disjoint, which is exactly the
condition under which fusion beats either sensor alone.

What this module does
---------------------
1. `detect_imu_reps`   - turns raw MPU6050 samples into rep events using the
                         same hysteresis FSM idea as the camera path, so the
                         two modalities are methodologically comparable.
2. `estimate_offset`   - the ESP32 clock and the browser clock are never
                         synchronised. We recover the constant offset by
                         searching for the shift that maximises agreement.
3. `match_events`      - greedy nearest-neighbour pairing inside a tolerance
                         window, after offset correction.
4. `fuse`              - decides the final rep list, gating singletons on
                         *why* the other sensor might have missed them.
5. `evaluate`          - precision / recall / F1 / count error against ground
                         truth. This produces the results table in the report.

Every threshold is a named constant or a function argument so the evaluation
script can sweep them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# --------------------------------------------------------------------------
# Defaults. Overridable per call so the sweep in run_evaluation.py is honest.
# --------------------------------------------------------------------------
DEFAULT_MATCH_WINDOW_S = 0.60
DEFAULT_MAX_OFFSET_S = 2.50
DEFAULT_OFFSET_STEP_S = 0.02
LOW_VIS_THRESHOLD = 0.55
DEFAULT_CADENCE_GATE = 0.45     # reject single-sensor reps closer than this x cadence
CONF_MATCHED = 0.97
CONF_CAMERA_ONLY = 0.62
CONF_IMU_ONLY_OCCLUDED = 0.74
CONF_REJECTED = 0.20


# ==========================================================================
# 1. IMU rep detection
# ==========================================================================
@dataclass
class ImuRep:
    index: int
    t: float
    peak_amp: float
    duration_s: float


def _moving_mean(x: Sequence[float], w: int) -> list[float]:
    """Causal trailing mean. Used by the streaming detector, which cannot
    look into the future."""
    if w <= 1:
        return list(x)
    out, acc, q = [], 0.0, []
    for v in x:
        q.append(v)
        acc += v
        if len(q) > w:
            acc -= q.pop(0)
        out.append(acc / len(q))
    return out


def _detrend(x: Sequence[float]) -> list[float]:
    """
    Remove sensor bias and slow drift without touching the rep signal.

    Two terms, in this order:
      1. a least-squares straight line  -> thermal drift of the MPU6050 bias
      2. the median of the residual     -> the constant zero-rate offset

    A moving average was the obvious choice here and is the wrong one: its
    window is comparable to one rep period, so near the start and end of a
    recording the truncated window is dominated by half a rep and the
    "baseline" cancels the very peak we are trying to detect. That bug cost
    the first rep of every session. A line has no window and therefore no
    boundary artefact, and gyro bias genuinely is a slowly varying offset,
    so this is also the more physically honest model.
    """
    n = len(x)
    if n < 3:
        return list(x)
    mean_i = (n - 1) / 2.0
    mean_x = sum(x) / n
    sxx = sum((i - mean_i) ** 2 for i in range(n))
    sxy = sum((i - mean_i) * (v - mean_x) for i, v in enumerate(x))
    slope = sxy / sxx if sxx else 0.0
    res = [v - (mean_x + slope * (i - mean_i)) for i, v in enumerate(x)]
    s = sorted(res)
    med = s[n // 2]
    return [v - med for v in res]


def _quantile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


# Normalising constants: for standard-normal data, the q-quantile of |x| is
# Phi^-1((1+q)/2). Dividing by it makes _robust_scale return the true sigma
# on pure noise, so the absolute activity floors stay in physical units.
_Z = {0.50: 0.6745, 0.75: 1.1503, 0.80: 1.2816, 0.90: 1.6449}


def _robust_scale(x: Sequence[float], q: float = 0.80) -> float:
    """
    Dispersion of a signal that is only intermittently active.

    This is NOT the median absolute deviation, and the difference matters.
    MAD is robust to a minority of outliers - but a set of repetitions with
    rests between them is only active maybe half the time, so MAD treats the
    MOVEMENT as the outlier and collapses to the noise floor. Measured on a
    real trace: a gyro axis swinging +/-410 deg/s reported a MAD of 7 deg/s,
    which sat under the activity gate and made the detector ignore the set
    entirely.

    Taking a high quantile (default the 80th) of |x - median| instead keeps
    the scale proportional to the movement down to roughly a 20% duty cycle,
    while still discarding the top 20% - which is where knocks and dropped
    weights live.
    """
    if not x:
        return 1e-6
    srt = sorted(x)
    med = srt[len(srt) // 2]
    dev = sorted(abs(v - med) for v in x)
    return max(1e-6, _quantile(dev, q) / _Z.get(q, 1.2816))


def _mad_std(x: Sequence[float]) -> float:
    """Kept for callers that want the strict median absolute deviation."""
    if not x:
        return 1.0
    srt = sorted(x)
    med = srt[len(srt) // 2]
    dev = sorted(abs(v - med) for v in x)
    return max(1e-6, 1.4826 * dev[len(dev) // 2])


def _sample_rate(ts: Sequence[float]) -> float:
    if len(ts) < 2:
        return 50.0
    span = ts[-1] - ts[0]
    return (len(ts) - 1) / span if span > 0 else 50.0


GYRO_ACTIVE_DEG_S = 8.0        # below this the gyro is not seeing real rotation
ACCEL_ACTIVE_MS2 = 0.35        # below this the accelerometer is only seeing noise

# An ABSOLUTE floor matters more than it looks. Every threshold in this file
# is relative (k x robust sigma), which means a completely idle sensor still
# crosses its own noise-derived threshold and manufactures repetitions out of
# nothing. These two constants are the gate that says "no real movement here".


def imu_signal(
    samples: Sequence[dict[str, float]],
) -> tuple[list[float], list[float], str]:
    """
    Reduce 6-DoF to ONE SIGNED scalar that completes one full cycle per rep.

    Why signed, and why a single axis:

    A repetition is a there-and-back movement, so the angular velocity about
    the working axis is positive through the concentric phase and negative
    through the eccentric. That sign change is the cleanest possible rep
    delimiter. Taking the magnitude - the obvious first implementation -
    rectifies the sine into two identical humps per rep and throws away the
    only feature that distinguishes "one rep" from "two half-reps"; it is
    also why the first version of this function undercounted badly.

    Axis choice is by robust dispersion: the axis the equipment actually
    rotates about carries the most energy. If no gyro axis is active (the
    sensor is mounted where the motion is translation, not rotation) we fall
    back to the most active accelerometer axis, which has the same
    there-and-back sign structure.
    """
    ts = [float(s["t"]) for s in samples]

    def prep(key: str) -> list[float]:
        return _detrend([float(s.get(key, 0.0)) for s in samples])

    gyro = {k: prep(k) for k in ("gx", "gy", "gz")}
    best_g = max(gyro, key=lambda k: _robust_scale(gyro[k]))
    if _robust_scale(gyro[best_g]) >= GYRO_ACTIVE_DEG_S:
        return ts, gyro[best_g], f"gyro:{best_g}"

    accel = {k: prep(k) for k in ("ax", "ay", "az")}
    best_a = max(accel, key=lambda k: _robust_scale(accel[k]))
    return ts, accel[best_a], f"accel:{best_a}"


def _schmitt_cycles(
    ts: Sequence[float],
    sig: Sequence[float],
    hi: float,
    min_rep_s: float,
    max_rep_s: float,
    settle_frac: float = 0.35,
) -> list[ImuRep]:
    """
    Count full cycles with a Schmitt trigger on the signed signal.

    State machine:
        low     --(signal rises above +hi)--> high     [first phase seen]
        high    --(signal falls below -hi)--> closing  [return phase seen]
        closing --(|signal| falls below lo)--> low     [motion stopped => ONE REP]

    The third state is what makes the IMU timestamp comparable with the
    camera one. pose_engine emits a rep when the joint is back at the start
    position, i.e. when the movement has stopped. If the IMU instead emitted
    at the -hi crossing it would fire while the limb is still travelling -
    consistently half a rep early - and the matcher would be asked to absorb
    a systematic lag that is an artefact of our own event definition rather
    than a real clock difference. Both modalities now mean the same thing by
    "a rep happened at time t".

    Requiring both signed crossings is also what makes this robust to a
    knock: a bump is a one-directional spike, so it trips the first edge and
    never the second. A hard strike that makes the equipment ring does
    oscillate through both and is counted - which is exactly the IMU false
    positive the camera is there to veto.
    """
    lo = hi * settle_frac
    reps: list[ImuRep] = []
    state = "low"
    start_t = 0.0
    peak = 0.0
    last_rep_t = -1e9

    for t, v in zip(ts, sig):
        if state == "low":
            if v > hi:
                state = "high"
                start_t = t
                peak = abs(v)
            continue

        peak = max(peak, abs(v))
        if t - start_t > max_rep_s:
            state = "low"                       # stalled mid-rep, abandon it
            continue

        if state == "high":
            if v < -hi:
                state = "closing"
        elif abs(v) < lo:                       # state == "closing"
            state = "low"
            if (t - last_rep_t) >= min_rep_s:
                reps.append(ImuRep(len(reps) + 1, round(t, 3),
                                   round(peak, 4), round(t - start_t, 3)))
                last_rep_t = t
    return reps


def detect_imu_reps(
    samples: Sequence[dict[str, float]],
    *,
    k: float = 0.8,
    min_rep_s: float = 0.55,
    max_rep_s: float = 12.0,
) -> list[ImuRep]:
    """
    Offline IMU rep counter. This is the version the report evaluates.

    `k` is the Schmitt threshold in robust sigmas. It is the IMU analogue of
    the hysteresis band between `start_thresh` and `peak_thresh` in
    pose_engine, which keeps the two modalities methodologically comparable.

    The detector is run on both signal polarities and the better run is kept:
    whether a rep begins with rotation one way or the other depends on how the
    user strapped the sensor on, and demanding a particular mounting
    orientation would be a usability bug dressed up as an algorithm.
    """
    if len(samples) < 16:
        return []
    ts, sig, src = imu_signal(samples)
    sigma = _robust_scale(sig)

    # An idle sensor (nobody strapped it on, or it fell off) must report zero
    # reps, not noise-derived ones. Without this gate a session with a dead
    # IMU silently contributes phantom events to the fusion layer.
    floor = GYRO_ACTIVE_DEG_S if src.startswith("gyro") else ACCEL_ACTIVE_MS2
    if sigma < floor:
        return []
    hi = k * sigma

    forward = _schmitt_cycles(ts, sig, hi, min_rep_s, max_rep_s)
    reverse = _schmitt_cycles(ts, [-v for v in sig], hi, min_rep_s, max_rep_s)
    return forward if len(forward) >= len(reverse) else reverse


# ==========================================================================
# 2. Clock offset estimation
# ==========================================================================
def estimate_offset(
    cam_times: Sequence[float],
    imu_times: Sequence[float],
    *,
    max_offset_s: float = DEFAULT_MAX_OFFSET_S,
    step_s: float = DEFAULT_OFFSET_STEP_S,
    window_s: float = DEFAULT_MATCH_WINDOW_S,
) -> tuple[float, float]:
    """
    Recover the constant clock offset between the two devices.

    Returns (offset_seconds, score). `offset` is what you ADD to an IMU
    timestamp to express it on the camera clock.

    Brute-force grid search. With at most a few hundred reps and a 2.5 s
    search range at 20 ms steps this is ~250 x n x m comparisons - microseconds
    of work, and far more robust on short sequences than cross-correlating
    sparse event trains.
    """
    if not cam_times or not imu_times:
        return 0.0, 0.0

    best_off, best_score = 0.0, -1.0
    n_steps = int((2 * max_offset_s) / step_s) + 1
    for i in range(n_steps):
        off = -max_offset_s + i * step_s
        shifted = [t + off for t in imu_times]
        pairs = _greedy_pairs(cam_times, shifted, window_s)
        if not pairs:
            continue
        residual = sum(abs(cam_times[a] - shifted[b]) for a, b in pairs) / len(pairs)
        # Prefer many matches first, tight residual second.
        score = len(pairs) - (residual / window_s) * 0.5
        if score > best_score:
            best_score, best_off = score, off
    return round(best_off, 3), round(max(0.0, best_score), 3)


def _greedy_pairs(
    a_times: Sequence[float],
    b_times: Sequence[float],
    window_s: float,
) -> list[tuple[int, int]]:
    """
    Greedy 1-to-1 matching, closest pair first.

    Greedy (not Hungarian) because rep events are monotonic in time and well
    separated; the optimal assignment and the greedy one coincide in practice,
    and greedy is trivial to explain in a viva.
    """
    cands: list[tuple[float, int, int]] = []
    for i, ta in enumerate(a_times):
        for j, tb in enumerate(b_times):
            d = abs(ta - tb)
            if d <= window_s:
                cands.append((d, i, j))
    cands.sort()
    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, i, j in cands:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append((i, j))
    return sorted(pairs)


# ==========================================================================
# 3/4. Fusion
# ==========================================================================
@dataclass
class FusedRep:
    index: int
    t: float
    origin: str                 # "both" | "camera_only" | "imu_only"
    accepted: bool
    confidence: float
    form_score: float = 1.0
    rom_deg: float = 0.0
    tempo_s: float = 0.0
    visibility: float = 1.0
    flags: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class FusionResult:
    offset_s: float
    offset_score: float
    matched: int
    camera_only: int
    imu_only: int
    camera_count: int
    imu_count: int
    fused_count: int
    reps: list[FusedRep]
    mean_confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "offset_s": self.offset_s,
            "offset_score": self.offset_score,
            "matched": self.matched,
            "camera_only": self.camera_only,
            "imu_only": self.imu_only,
            "camera_count": self.camera_count,
            "imu_count": self.imu_count,
            "fused_count": self.fused_count,
            "mean_confidence": self.mean_confidence,
            "reps": [r.__dict__ for r in self.reps],
        }


def _visibility_at(timeline: Sequence[tuple[float, float]], t: float) -> float:
    """Nearest-sample camera visibility at time t. 1.0 when unknown."""
    if not timeline:
        return 1.0
    best = min(timeline, key=lambda kv: abs(kv[0] - t))
    return best[1] if abs(best[0] - t) < 1.5 else 1.0


def _median(xs: Sequence[float]) -> float:
    v = sorted(xs)
    n = len(v)
    if n == 0:
        return 0.0
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def fuse(
    camera_reps: Sequence[dict[str, Any]],
    imu_reps: Sequence[ImuRep] | Sequence[dict[str, Any]],
    *,
    visibility_timeline: Sequence[tuple[float, float]] = (),
    match_window_s: float = DEFAULT_MATCH_WINDOW_S,
    max_offset_s: float = DEFAULT_MAX_OFFSET_S,
    low_vis_threshold: float = LOW_VIS_THRESHOLD,
    cadence_gate: float = DEFAULT_CADENCE_GATE,
    known_offset_s: float | None = None,
) -> FusionResult:
    """
    Reconcile camera reps and IMU reps into one authoritative list.

    Decision policy
    ---------------
    both
        Accept. Two independent sensors agreeing is the strongest evidence
        available, and these anchor the session's cadence estimate.

    camera_only
        Three ways to lose. Rejected if the camera could not see the joint
        (landmark jitter invents reps exactly when visibility is poor).
        Rejected if it arrives far too soon after the previous accepted rep -
        see the cadence gate below. Otherwise accepted: a clearly-seen rep
        with the IMU silent is usually a real rep the sensor missed, which is
        the whole reason the camera is in the system.

    imu_only
        Accepted only if the camera was occluded at that moment. If the
        camera had a clear view and still saw nothing, the IMU event is a
        bump, a dropped weight, or a re-rack - not a repetition.

    The cadence gate
    ----------------
    Repetitions inside a working set are quasi-periodic; fidgets are not. We
    take the median interval between confirmed (matched) reps as the set's
    cadence, and reject any single-sensor event landing closer than
    `cadence_gate` x that interval to the previously accepted rep.

    This is what separates "the IMU missed a real rep" from "the camera
    hallucinated a rep": the former lands on the beat, the latter lands just
    after a real rep, while the user is still adjusting their grip. Without
    it, the camera-only branch has no way to tell the two apart and the
    fused count inherits every camera false positive.
    """
    cam_times = [float(r["end_ts"]) for r in camera_reps]
    imu_list = [r if isinstance(r, ImuRep) else ImuRep(
        int(r.get("index", 0)), float(r["t"]), float(r.get("peak_amp", 0.0)),
        float(r.get("duration_s", 0.0))) for r in imu_reps]
    imu_times = [r.t for r in imu_list]

    if known_offset_s is not None:
        offset, score = float(known_offset_s), 0.0
    else:
        offset, score = estimate_offset(
            cam_times, imu_times, max_offset_s=max_offset_s, window_s=match_window_s
        )

    shifted = [t + offset for t in imu_times]
    pairs = _greedy_pairs(cam_times, shifted, match_window_s)
    cam_matched = {i for i, _ in pairs}
    imu_matched = {j for _, j in pairs}

    # ---- cadence reference, taken from confirmed reps only ----
    confirmed = sorted(cam_times[i] for i in cam_matched)
    if len(confirmed) >= 3:
        cadence = _median([b - a for a, b in zip(confirmed, confirmed[1:])])
    elif len(cam_times) >= 3:
        srt = sorted(cam_times)
        cadence = _median([b - a for a, b in zip(srt, srt[1:])])
    else:
        cadence = 0.0
    min_gap = cadence * cadence_gate if cadence > 0 else 0.0

    # ---- build every candidate, then decide in chronological order ----
    candidates: list[FusedRep] = []
    for i, crep in enumerate(camera_reps):
        vis = float(crep.get("mean_visibility", 1.0))
        candidates.append(FusedRep(
            0, float(crep["end_ts"]),
            "both" if i in cam_matched else "camera_only",
            False, 0.0,
            form_score=float(crep.get("form_score", 1.0)),
            rom_deg=float(crep.get("rom_deg", 0.0)),
            tempo_s=float(crep.get("tempo_s", 0.0)),
            visibility=vis,
            flags=list(crep.get("flags", [])),
        ))
    for j, irep in enumerate(imu_list):
        if j in imu_matched:
            continue
        t = shifted[j]
        candidates.append(FusedRep(
            0, t, "imu_only", False, 0.0,
            visibility=_visibility_at(visibility_timeline, t),
        ))

    candidates.sort(key=lambda r: r.t)

    last_accepted_t: float | None = None
    for r in candidates:
        too_soon = (
            min_gap > 0.0
            and last_accepted_t is not None
            and (r.t - last_accepted_t) < min_gap
        )
        if r.origin == "both":
            r.accepted, r.confidence = True, CONF_MATCHED
            r.reason = "camera and IMU agree"
        elif r.origin == "camera_only":
            if r.visibility < low_vis_threshold:
                r.accepted, r.confidence = False, CONF_REJECTED
                r.reason = "low visibility and unconfirmed by IMU"
                r.flags = [*r.flags, "low_visibility"]
            elif too_soon:
                r.accepted, r.confidence = False, CONF_REJECTED
                r.reason = (f"off-cadence: {r.t - last_accepted_t:.2f}s after the "
                            f"previous rep, set cadence is {cadence:.2f}s")
                r.flags = [*r.flags, "off_cadence"]
            else:
                r.accepted, r.confidence = True, CONF_CAMERA_ONLY
                r.reason = "clear camera view, IMU silent"
        else:  # imu_only
            if r.visibility >= low_vis_threshold:
                r.accepted, r.confidence = False, CONF_REJECTED
                r.reason = "camera had a clear view and saw no rep"
                r.flags = ["unconfirmed_imu"]
            elif too_soon:
                r.accepted, r.confidence = False, CONF_REJECTED
                r.reason = "off-cadence IMU event during occlusion"
                r.flags = ["unconfirmed_imu", "off_cadence"]
            else:
                r.accepted, r.confidence = True, CONF_IMU_ONLY_OCCLUDED
                r.reason = "camera occluded, IMU observed motion"
                r.flags = ["camera_occluded"]

        if r.accepted:
            last_accepted_t = r.t

    accepted = [r for r in candidates if r.accepted]
    for n, r in enumerate(accepted, start=1):
        r.index = n

    conf = sum(r.confidence for r in accepted) / len(accepted) if accepted else 0.0
    return FusionResult(
        offset_s=offset,
        offset_score=score,
        matched=len(pairs),
        camera_only=sum(1 for r in candidates if r.origin == "camera_only" and r.accepted),
        imu_only=sum(1 for r in candidates if r.origin == "imu_only" and r.accepted),
        camera_count=len(cam_times),
        imu_count=len(imu_times),
        fused_count=len(accepted),
        reps=candidates,
        mean_confidence=round(conf, 3),
    )


# ==========================================================================
# 5. Evaluation
# ==========================================================================
def evaluate(
    pred_times: Sequence[float],
    gt_times: Sequence[float],
    *,
    window_s: float = DEFAULT_MATCH_WINDOW_S,
    allow_offset: bool = False,
) -> dict[str, float]:
    """
    Standard detection metrics against a human-labelled ground truth.

    A predicted rep is a true positive if it pairs 1-to-1 with a ground-truth
    rep within `window_s`. Anything else is a false positive (hallucinated
    rep) or a false negative (missed rep).

    `count_error` is reported separately because for a user the count is what
    matters, and a system can have a perfect count while mistiming every rep.
    """
    pt = list(pred_times)
    gt = list(gt_times)
    if allow_offset and pt and gt:
        off, _ = estimate_offset(gt, pt, window_s=window_s)
        pt = [t + off for t in pt]

    pairs = _greedy_pairs(gt, pt, window_s)
    tp = len(pairs)
    fp = len(pt) - tp
    fn = len(gt) - tp
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    timing = [abs(gt[a] - pt[b]) for a, b in pairs]
    return {
        "n_gt": len(gt),
        "n_pred": len(pt),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "count_error": len(pt) - len(gt),
        "abs_count_error": abs(len(pt) - len(gt)),
        "count_accuracy": round(1 - abs(len(pt) - len(gt)) / len(gt), 4) if gt else 0.0,
        "mean_timing_error_ms": round(1000 * sum(timing) / len(timing), 1) if timing else 0.0,
    }


def aggregate(rows: Iterable[dict[str, float]]) -> dict[str, float]:
    """Micro-average a list of per-session metric dicts."""
    rows = list(rows)
    if not rows:
        return {}
    tp = sum(r["tp"] for r in rows)
    fp = sum(r["fp"] for r in rows)
    fn = sum(r["fn"] for r in rows)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r_ = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r_ / (p + r_) if (p + r_) else 0.0
    n_gt = sum(r["n_gt"] for r in rows)
    return {
        "sessions": len(rows),
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(p, 4),
        "recall": round(r_, 4),
        "f1": round(f1, 4),
        "mae_count": round(sum(r["abs_count_error"] for r in rows) / len(rows), 3),
        "mape_count_pct": round(
            100 * sum(r["abs_count_error"] for r in rows) / n_gt, 2) if n_gt else 0.0,
        "mean_timing_error_ms": round(
            sum(r["mean_timing_error_ms"] for r in rows) / len(rows), 1),
    }


# ==========================================================================
# Online (live) fusion used by the WebSocket
# ==========================================================================
class OnlineFusion:
    """
    Streaming version of `fuse` for a live session.

    The offline function is the one the report evaluates. This class exists so
    the demo can show a fused count updating in real time; it re-runs the
    offline algorithm over the buffers whenever either sensor fires, which is
    cheap at demo scale (tens of reps) and guarantees the live number and the
    reported number come from the same code path.
    """

    def __init__(
        self,
        match_window_s: float = DEFAULT_MATCH_WINDOW_S,
        low_vis_threshold: float = LOW_VIS_THRESHOLD,
    ) -> None:
        self.match_window_s = match_window_s
        self.low_vis_threshold = low_vis_threshold
        self.camera_reps: list[dict[str, Any]] = []
        self.imu_reps: list[ImuRep] = []
        self.visibility_timeline: list[tuple[float, float]] = []
        self.last: FusionResult | None = None

    def add_camera_rep(self, rep: dict[str, Any]) -> FusionResult:
        self.camera_reps.append(rep)
        return self.recompute()

    def add_imu_rep(self, t: float, peak_amp: float = 0.0) -> FusionResult:
        self.imu_reps.append(ImuRep(len(self.imu_reps) + 1, float(t), peak_amp, 0.0))
        return self.recompute()

    def note_visibility(self, t: float, vis: float) -> None:
        # One sample per ~0.5 s keeps the timeline small but dense enough.
        if not self.visibility_timeline or t - self.visibility_timeline[-1][0] > 0.5:
            self.visibility_timeline.append((t, vis))

    def recompute(self) -> FusionResult:
        self.last = fuse(
            self.camera_reps,
            self.imu_reps,
            visibility_timeline=self.visibility_timeline,
            match_window_s=self.match_window_s,
            low_vis_threshold=self.low_vis_threshold,
        )
        return self.last

    def has_imu(self) -> bool:
        return bool(self.imu_reps)


# ==========================================================================
# Streaming IMU detector (used by the live MQTT/HTTP path)
# ==========================================================================
class StreamingImuDetector:
    """
    Online twin of `detect_imu_reps`.

    Same three-state Schmitt trigger on a signed axis, but the baseline, the
    robust sigma and the choice of axis all come from a trailing window.
    `detect_imu_reps` is still the version the report evaluates; this exists
    so the live demo shows the IMU count climbing in real time.

    Two things the first version of this class got wrong, both worth knowing:

    1. It chose the working axis once, at warm-up. Warm-up happens while the
       user is standing still, so the gyro is flat and the "most active axis"
       is whichever accelerometer channel is noisiest. It then stayed locked
       to that noise channel for the rest of the session.
    2. Its threshold was purely relative (k x sigma), so that noise channel
       comfortably crossed its own threshold and reported 65 reps for a
       12-rep set.

    The fix is an absolute activity floor plus periodic re-selection: the axis
    is re-chosen every `RESELECT_EVERY` samples, and if no axis clears the
    floor the detector reports nothing at all.
    """

    GYRO_AXES = ("gx", "gy", "gz")
    ACCEL_AXES = ("ax", "ay", "az")
    RESELECT_EVERY = 25

    def __init__(
        self,
        window: int = 300,
        k: float = 0.8,
        min_rep_s: float = 0.55,
        max_rep_s: float = 12.0,
        warmup: int = 40,
        settle_frac: float = 0.35,
    ) -> None:
        from collections import deque

        self.window = window
        self.k = k
        self.min_rep_s = min_rep_s
        self.max_rep_s = max_rep_s
        self.warmup = warmup
        self.settle_frac = settle_frac

        self.buf: dict[str, Any] = {
            a: deque(maxlen=window) for a in (*self.GYRO_AXES, *self.ACCEL_AXES)
        }
        self.axis: str | None = None
        self.state = "low"
        self.start_t = 0.0
        self.peak = 0.0
        self.last_rep_t = -1e9
        self.count = 0
        self.n = 0

    # ------------------------------------------------------------------ #
    def _sigma_of(self, axis: str) -> float:
        vals = list(self.buf[axis])
        if len(vals) < 8:
            return 0.0
        mean = sum(vals) / len(vals)
        return _robust_scale([v - mean for v in vals])

    def _select_axis(self) -> str | None:
        """Prefer a genuinely rotating gyro axis; fall back to accelerometer."""
        g = {a: self._sigma_of(a) for a in self.GYRO_AXES}
        best_g = max(g, key=lambda a: g[a])
        if g[best_g] >= GYRO_ACTIVE_DEG_S:
            return best_g
        acc = {a: self._sigma_of(a) for a in self.ACCEL_AXES}
        best_a = max(acc, key=lambda a: acc[a])
        if acc[best_a] >= ACCEL_ACTIVE_MS2:
            return best_a
        return None                      # sensor is idle - report nothing

    def push(self, s: dict[str, float]) -> ImuRep | None:
        t = float(s.get("t") or 0.0)
        for a in (*self.GYRO_AXES, *self.ACCEL_AXES):
            self.buf[a].append(float(s.get(a, 0.0)))
        self.n += 1
        if self.n < self.warmup:
            return None

        if self.axis is None or self.n % self.RESELECT_EVERY == 0:
            chosen = self._select_axis()
            if chosen != self.axis:
                self.axis = chosen
                self.state = "low"       # a new axis invalidates the FSM
        if self.axis is None:
            return None

        vals = list(self.buf[self.axis])
        baseline = sum(vals) / len(vals)
        sigma = _robust_scale([v - baseline for v in vals])
        floor = GYRO_ACTIVE_DEG_S if self.axis in self.GYRO_AXES else ACCEL_ACTIVE_MS2
        if sigma < floor:
            self.state = "low"
            return None

        hi = self.k * sigma
        lo = hi * self.settle_frac
        v = vals[-1] - baseline

        if self.state == "low":
            if v > hi:
                self.state = "high"
                self.start_t = t
                self.peak = abs(v)
            return None

        self.peak = max(self.peak, abs(v))
        if t - self.start_t > self.max_rep_s:
            self.state = "low"
            return None

        if self.state == "high":
            if v < -hi:
                self.state = "closing"
            return None

        # state == "closing": wait for the movement to actually stop, so the
        # timestamp means the same thing as the camera's rep timestamp.
        if abs(v) >= lo:
            return None
        self.state = "low"
        if (t - self.last_rep_t) < self.min_rep_s:
            return None
        self.last_rep_t = t
        self.count += 1
        return ImuRep(self.count, round(t, 3), round(self.peak, 4),
                      round(t - self.start_t, 3))
