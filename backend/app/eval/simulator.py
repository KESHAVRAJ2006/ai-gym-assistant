"""
Controlled-condition simulator for the fusion evaluation.

READ THIS BEFORE QUOTING ANY NUMBER IT PRODUCES
-----------------------------------------------
This generates *synthetic sensor data with known ground truth*. It does NOT
generate results. What it does is let you measure camera-only, IMU-only and
fused counting under conditions you can dial exactly - 40% occlusion, a loose
strap, someone knocking the dumbbell - which you cannot do repeatably with a
human in front of a webcam.

Crucially, it does not fake the detectors. It synthesises 33 MediaPipe-shaped
landmarks and raw 6-DoF IMU samples, then runs the *real* `PoseEngine` and the
*real* `detect_imu_reps` / `fuse` over them. Every number therefore exercises
the code that ships.

In the report this is the "controlled ablation" table. It must sit next to a
second table from real recorded sessions (scripts/run_evaluation.py --source
real). Simulation alone is not a validation of the contribution; it is the
part of the validation that establishes *why* fusion helps and *when* it does
not. Say exactly that in the write-up.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from app.engines.pose_engine import PoseEngine

# --------------------------------------------------------------------------
# Skeleton geometry (metres, MediaPipe world-landmark convention:
# origin at the hip midpoint, +y points DOWN, +x to the subject's left)
# --------------------------------------------------------------------------
UPPER_ARM_M = 0.28
FOREARM_M = 0.26

_STATIC = {
    0:  (0.00, -0.62, 0.0),    # nose
    11: (-0.18, -0.48, 0.0),   # left shoulder
    12: (0.18, -0.48, 0.0),    # right shoulder
    23: (-0.11, 0.00, 0.0),    # left hip
    24: (0.11, 0.00, 0.0),     # right hip
    25: (-0.11, 0.45, 0.0),    # left knee
    26: (0.11, 0.45, 0.0),     # right knee
    27: (-0.11, 0.88, 0.0),    # left ankle
    28: (0.11, 0.88, 0.0),     # right ankle
    29: (-0.11, 0.92, 0.0),
    30: (0.11, 0.92, 0.0),
    31: (-0.11, 0.95, 0.06),   # left foot index
    32: (0.11, 0.95, 0.06),
}


@dataclass
class Condition:
    """One experimental cell. Every field is a knob the report can cite."""

    name: str
    n_reps: int = 12
    period_s: float = 2.6
    cam_fps: float = 20.0
    imu_hz: float = 50.0

    # camera degradations
    occlusion_frac: float = 0.0      # fraction of reps where the arm is hidden
    landmark_noise_m: float = 0.004  # per-axis Gaussian noise on world landmarks
    twitches: int = 0                # rest-position fidgets the camera may miscount

    # IMU degradations
    imu_miss_prob: float = 0.0       # reps where the strap slips (low amplitude)
    imu_noise: float = 3.0           # deg/s Gaussian noise
    bumps: int = 0                   # knocks the camera cannot see

    # cross-device
    clock_offset_s: float = 0.0      # IMU clock minus camera clock
    tempo_jitter: float = 0.12       # per-rep period variation (fraction)
    rest_s: float = 1.1              # pause between reps, where fidgets happen


@dataclass
class SimOutput:
    condition: str
    gt_times: list[float]
    cam_frames: list[dict[str, Any]] = field(default_factory=list)
    imu_samples: list[dict[str, float]] = field(default_factory=list)
    true_offset_s: float = 0.0


# --------------------------------------------------------------------------
def _angle_profile(phase: float, rest: float, flex: float) -> float:
    """
    Raised-cosine flexion cycle: rest -> flex -> rest over one period.

    A raised cosine (not a triangle wave) because real limb motion has zero
    velocity at both end points, and the IMU trace we derive from this is the
    derivative - a triangle wave would give a physically impossible square
    angular-velocity signal.
    """
    return rest + (flex - rest) * (0.5 - 0.5 * math.cos(2 * math.pi * phase))


def _arm_landmarks(elbow_angle_deg: float) -> dict[int, tuple[float, float, float]]:
    """Place the left elbow/wrist for a given elbow angle; everything else static."""
    sx, sy, sz = _STATIC[11]
    ex, ey, ez = sx, sy + UPPER_ARM_M, sz          # elbow hangs below the shoulder

    # Angle is measured at the elbow between (elbow->shoulder) and (elbow->wrist).
    # (elbow->shoulder) is (0,-1,0), so a forearm direction of
    # (sin t, -cos t, 0) subtends exactly t.
    t = math.radians(elbow_angle_deg)
    fx, fy, fz = -math.sin(t), -math.cos(t), 0.0
    wx, wy, wz = ex + FOREARM_M * fx, ey + FOREARM_M * fy, ez + FOREARM_M * fz

    return {13: (ex, ey, ez), 15: (wx, wy, wz),
            14: (-ex, ey, ez), 16: (-wx, wy, wz)}


def _to_image(p: tuple[float, float, float]) -> tuple[float, float, float]:
    """Crude orthographic projection into normalised image coordinates."""
    return (0.5 + p[0] / 1.6, 0.5 + p[1] / 2.2, p[2])


def simulate(cond: Condition, seed: int = 0) -> SimOutput:
    """Produce one synthetic session: camera frames + IMU samples + ground truth."""
    rng = random.Random(seed)
    # Real anatomical extremes for a curl, deliberately WIDER than the
    # engine's thresholds (150 / 60) so the hysteresis band is exercised
    # rather than skimmed.
    rest, flex = 168.0, 38.0

    # ---- rep timeline with human tempo variation ----
    periods = [cond.period_s * (1 + rng.gauss(0, cond.tempo_jitter)) for _ in range(cond.n_reps)]
    periods = [max(1.0, p) for p in periods]
    starts, rests, acc = [], [], 1.0     # 1 s of standing still before rep 1
    for p in periods:
        starts.append(acc)
        acc += p
        rests.append((acc, acc + cond.rest_s))   # the pause after this rep
        acc += cond.rest_s
    total = acc + 1.0
    gt_times = [s + p for s, p in zip(starts, periods)]

    occluded = set(rng.sample(range(cond.n_reps),
                              k=int(round(cond.occlusion_frac * cond.n_reps))))
    slipped = {i for i in range(cond.n_reps) if rng.random() < cond.imu_miss_prob}
    # Fidgets must land in the PAUSES between reps. Placing them uniformly
    # over the whole session silently buried them inside rep windows, where
    # the rep profile overwrote them - the C3 cell then measured nothing.
    TWITCH_S = 0.8
    usable = [r for r in rests if (r[1] - r[0]) > TWITCH_S + 0.1]
    picked = rng.sample(usable, k=min(cond.twitches, len(usable))) if usable else []
    twitch_times = sorted(rng.uniform(a, b - TWITCH_S) for a, b in picked)
    bump_times = sorted(rng.uniform(1.0, total - 1.0) for _ in range(cond.bumps))

    def angle_at(t: float) -> tuple[float, int | None]:
        """True elbow angle at time t, and which rep index is active."""
        for i, (s, p) in enumerate(zip(starts, periods)):
            if s <= t < s + p:
                return _angle_profile((t - s) / p, rest, flex), i
        # At rest between reps - a twitch is a partial curl that is NOT a rep.
        a = rest
        for tt in twitch_times:
            if 0 <= t - tt < TWITCH_S:
                a = _angle_profile((t - tt) / TWITCH_S, rest, 52.0)
        return a, None

    # ------------------------------------------------------------ camera --
    out = SimOutput(condition=cond.name, gt_times=gt_times,
                    true_offset_s=cond.clock_offset_s)
    dt = 1.0 / cond.cam_fps
    n_frames = int(total * cond.cam_fps)
    frozen: dict[int, tuple[float, float, float]] | None = None

    for k in range(n_frames):
        t = k * dt
        ang, rep_i = angle_at(t)
        arm = _arm_landmarks(ang)
        hidden = rep_i is not None and rep_i in occluded

        if hidden:
            # Real occlusion does not delete landmarks - MediaPipe keeps
            # emitting its last confident guess with low visibility. Freezing
            # is therefore the faithful failure mode, and it is what makes the
            # camera silently MISS the rep rather than mis-time it.
            if frozen is None:
                frozen = arm
            arm = frozen
        else:
            frozen = None

        wlm: list[list[float]] = []
        lm: list[list[float]] = []
        for idx in range(33):
            p = arm.get(idx) or _STATIC.get(idx) or (0.0, 0.0, 0.0)
            n = cond.landmark_noise_m * (4.0 if hidden else 1.0)
            p = (p[0] + rng.gauss(0, n), p[1] + rng.gauss(0, n), p[2] + rng.gauss(0, n))
            wlm.append([round(v, 5) for v in p])
            ix, iy, iz = _to_image(p)
            vis = 0.22 if (hidden and idx in (13, 15)) else rng.uniform(0.88, 0.99)
            lm.append([round(ix, 5), round(iy, 5), round(iz, 5), round(vis, 3)])

        out.cam_frames.append({"t": round(t, 4), "lm": lm, "wlm": wlm})

    # --------------------------------------------------------------- IMU --
    idt = 1.0 / cond.imu_hz
    n_imu = int(total * cond.imu_hz)
    for k in range(n_imu):
        t = k * idt
        a0, rep_i = angle_at(t)
        a1, _ = angle_at(t + idt)
        omega = (a1 - a0) / idt                      # deg/s, the honest derivative

        if rep_i is not None and rep_i in slipped:
            omega *= 0.12                            # strap slipped on the bar
        if rep_i is None:
            # The fidget is the user adjusting their grip or scratching while
            # the weight sits on the rack: the camera sees an arm bend, the
            # instrumented equipment does not move at all.
            omega *= 0.05

        for bt in bump_times:
            if 0 <= t - bt < 0.10:
                omega += 420.0 * math.sin(math.pi * (t - bt) / 0.10)

        out.imu_samples.append({
            "t": round(t + cond.clock_offset_s, 4),
            "ax": round(rng.gauss(0, cond.imu_noise * 0.02), 4),
            "ay": round(rng.gauss(0, cond.imu_noise * 0.02), 4),
            "az": round(9.81 + rng.gauss(0, cond.imu_noise * 0.02), 4),
            "gx": round(rng.gauss(0, cond.imu_noise), 3),
            "gy": round(omega + rng.gauss(0, cond.imu_noise), 3),
            "gz": round(rng.gauss(0, cond.imu_noise), 3),
        })

    return out


def run_pose_engine(sim: SimOutput, exercise: str = "bicep_curl") -> dict[str, Any]:
    """Replay the synthetic frames through the production PoseEngine."""
    eng = PoseEngine(exercise, side="left")
    for f in sim.cam_frames:
        eng.update(f["t"], f["lm"], f["wlm"])
    return {
        "reps": [r.to_dict() for r in eng.reps],
        "times": [r.end_ts for r in eng.reps],
        "summary": eng.summary(),
        "visibility_timeline": [(fr["t"], fr["vis"]) for fr in eng.frames],
    }


# --------------------------------------------------------------------------
# The experimental design
# --------------------------------------------------------------------------
def default_conditions() -> list[Condition]:
    """
    Six cells. The first is the control; each of the next four isolates ONE
    failure mode so the report can attribute the gain; the last combines them
    into a realistic gym scenario.
    """
    return [
        Condition("C1_clean", clock_offset_s=0.35),
        Condition("C2_occlusion_40pct", occlusion_frac=0.40, clock_offset_s=-0.80),
        Condition("C3_camera_false_reps", twitches=4, landmark_noise_m=0.010,
                  clock_offset_s=0.55, rest_s=1.4),
        Condition("C4_imu_strap_slip", imu_miss_prob=0.35, clock_offset_s=1.10),
        Condition("C5_imu_bumps", bumps=4, clock_offset_s=-1.40),
        Condition("C6_realistic_gym", occlusion_frac=0.25, twitches=2, bumps=2,
                  imu_miss_prob=0.15, landmark_noise_m=0.008, imu_noise=5.0,
                  clock_offset_s=0.90, rest_s=1.4),
    ]
