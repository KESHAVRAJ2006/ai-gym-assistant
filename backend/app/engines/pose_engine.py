"""
pose_engine - joint angles, repetition finite state machine, and form rules.

Input is 33 MediaPipe Pose landmarks per frame, streamed from the browser.
Video never leaves the client, which is both a privacy property worth stating
in the report and the reason this runs comfortably on a free Render dyno.

Two landmark sets arrive per frame:
  lm  : normalised image coordinates (x, y in 0..1, plus visibility).
        Used for on-screen geometry rules and for visibility gating.
  wlm : world coordinates in metres, origin at the hip midpoint.
        Used for joint angles, because normalised coordinates are distorted
        by the camera aspect ratio and would bias every angle we measure.

Public API
----------
    engine = PoseEngine("bicep_curl")
    result = engine.update(t, lm, wlm)      # once per frame
    result.rep_event                        # not None on the frame a rep ends
    engine.summary()
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any

# --------------------------------------------------------------------------
# MediaPipe Pose landmark indices (BlazePose 33-point topology).
# --------------------------------------------------------------------------
NOSE = 0
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28
L_HEEL, R_HEEL = 29, 30
L_FOOT, R_FOOT = 31, 32

SIDE_INDICES = {
    "left": {
        "shoulder": L_SHOULDER, "elbow": L_ELBOW, "wrist": L_WRIST,
        "hip": L_HIP, "knee": L_KNEE, "ankle": L_ANKLE, "foot": L_FOOT,
    },
    "right": {
        "shoulder": R_SHOULDER, "elbow": R_ELBOW, "wrist": R_WRIST,
        "hip": R_HIP, "knee": R_KNEE, "ankle": R_ANKLE, "foot": R_FOOT,
    },
}


# --------------------------------------------------------------------------
# Exercise definitions
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ExerciseConfig:
    key: str
    name: str
    joint: str               # which angle drives the state machine
    triple: tuple[str, str, str]
    direction: str           # "decrease" (flexion-driven) or "increase"
    start_thresh: float      # angle that means "back at the start position"
    peak_thresh: float       # angle that means "at the top of the movement"
    full_rom_deg: float      # ROM a textbook rep should cover
    min_rep_s: float = 0.55
    max_rep_s: float = 12.0
    mets: float = 5.0        # for calorie estimation

    @property
    def midpoint(self) -> float:
        return (self.start_thresh + self.peak_thresh) / 2.0


EXERCISES: dict[str, ExerciseConfig] = {
    "bicep_curl": ExerciseConfig(
        key="bicep_curl", name="Bicep Curl", joint="elbow",
        triple=("shoulder", "elbow", "wrist"),
        direction="decrease", start_thresh=150.0, peak_thresh=60.0,
        full_rom_deg=105.0, mets=3.5,
    ),
    "squat": ExerciseConfig(
        key="squat", name="Bodyweight Squat", joint="knee",
        triple=("hip", "knee", "ankle"),
        direction="decrease", start_thresh=160.0, peak_thresh=100.0,
        full_rom_deg=80.0, mets=5.5,
    ),
    "pushup": ExerciseConfig(
        key="pushup", name="Push-up", joint="elbow",
        triple=("shoulder", "elbow", "wrist"),
        direction="decrease", start_thresh=155.0, peak_thresh=95.0,
        full_rom_deg=70.0, mets=6.0,
    ),
    "shoulder_press": ExerciseConfig(
        key="shoulder_press", name="Shoulder Press", joint="elbow",
        triple=("shoulder", "elbow", "wrist"),
        direction="increase", start_thresh=95.0, peak_thresh=158.0,
        full_rom_deg=70.0, mets=4.5,
    ),
    "lateral_raise": ExerciseConfig(
        key="lateral_raise", name="Lateral Raise", joint="shoulder_abduction",
        triple=("hip", "shoulder", "elbow"),
        direction="increase", start_thresh=30.0, peak_thresh=78.0,
        full_rom_deg=55.0, mets=3.5,
    ),
}

DEFAULT_EXERCISE = "bicep_curl"

# ~5 minutes at 20 Hz. Beyond this we stop archiving raw landmarks rather
# than risk the process being OOM-killed mid-demo.
MAX_RAW_FRAMES = 6000


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------
def _vec(a: list[float], b: list[float]) -> tuple[float, float, float]:
    return (b[0] - a[0], b[1] - a[1], b[2] - a[2] if len(a) > 2 and len(b) > 2 else 0.0)


def angle_deg(a: list[float], b: list[float], c: list[float]) -> float:
    """
    Interior angle ABC in degrees, 0..180.

    Uses atan2 of the cross/dot magnitudes rather than acos(dot/|u||v|)
    because acos loses precision and can raise a domain error when the
    landmarks are almost collinear - which happens on every fully extended arm.
    """
    ux, uy, uz = _vec(b, a)
    vx, vy, vz = _vec(b, c)
    cx = uy * vz - uz * vy
    cy = uz * vx - ux * vz
    cz = ux * vy - uy * vx
    cross = math.sqrt(cx * cx + cy * cy + cz * cz)
    dot = ux * vx + uy * vy + uz * vz
    if cross == 0.0 and dot == 0.0:
        return 0.0
    return math.degrees(math.atan2(cross, dot))


def angle_from_vertical(top: list[float], bottom: list[float]) -> float:
    """Degrees the segment bottom->top leans away from the image vertical."""
    dx = top[0] - bottom[0]
    dy = top[1] - bottom[1]
    if dx == 0.0 and dy == 0.0:
        return 0.0
    return abs(math.degrees(math.atan2(abs(dx), abs(dy))))


class EMA:
    """One-pole exponential smoother. Kills MediaPipe single-frame jitter."""

    def __init__(self, alpha: float = 0.4) -> None:
        self.alpha = alpha
        self.value: float | None = None

    def push(self, x: float) -> float:
        self.value = x if self.value is None else self.alpha * x + (1 - self.alpha) * self.value
        return self.value


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
@dataclass
class RepEvent:
    index: int
    start_ts: float
    end_ts: float
    tempo_s: float
    rom_deg: float
    peak_angle: float
    min_angle: float
    form_score: float
    flags: list[str] = field(default_factory=list)
    mean_visibility: float = 1.0
    source: str = "camera"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FrameResult:
    t: float
    angles: dict[str, float]
    state: str
    progress: float               # 0..1 through the current half-rep
    rep_count: int
    visibility: float
    live_flags: list[str]
    rep_event: RepEvent | None = None
    ok: bool = True
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {
            "t": round(self.t, 3),
            "angles": {k: round(v, 1) for k, v in self.angles.items()},
            "state": self.state,
            "progress": round(self.progress, 3),
            "reps": self.rep_count,
            "visibility": round(self.visibility, 3),
            "flags": self.live_flags,
            "ok": self.ok,
            "message": self.message,
        }
        if self.rep_event is not None:
            d["rep"] = self.rep_event.to_dict()
        return d


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------
class PoseEngine:
    """
    Stateful, single-session rep counter.

    One instance per WebSocket connection. Not thread-safe by design - each
    connection owns its own instance, so there is nothing to lock.
    """

    MIN_TRACK_VIS = 0.5          # below this a joint is considered unreliable
    SIDE_LOCK_FRAMES = 15        # frames spent deciding which side faces camera

    def __init__(self, exercise: str = DEFAULT_EXERCISE, side: str = "auto") -> None:
        self.cfg = EXERCISES.get(exercise, EXERCISES[DEFAULT_EXERCISE])
        self.requested_side = side
        self.side = side if side in SIDE_INDICES else "left"
        self._side_locked = side in SIDE_INDICES
        self._side_votes = {"left": 0.0, "right": 0.0}
        self._frames_seen = 0

        self.smoother = EMA(alpha=0.45)
        self.state = "start"
        self.rep_count = 0

        # per-rep accumulators
        self._rep_start_ts: float | None = None
        self._rep_min = 180.0
        self._rep_max = 0.0
        self._rep_vis: list[float] = []
        self._rep_flags: set[str] = set()
        self._rep_penalty = 0.0

        self.reps: list[RepEvent] = []
        self.frames: list[dict[str, Any]] = []   # reduced angle trace, for charts
        # Raw landmarks, capped. This is what makes a recorded session
        # REPLAYABLE: without it the evaluation can only compare rep counts on
        # real data, never per-rep precision and recall, because PoseEngine
        # cannot be re-run with different thresholds. The cap keeps a runaway
        # session from exhausting a 512 MB dyno.
        self.raw_frames: list[dict[str, Any]] = []
        self.raw_dropped = 0
        self.first_ts: float | None = None
        self.last_ts: float | None = None
        self._vis_sum = 0.0
        self._vis_n = 0

    # ------------------------------------------------------------------ #
    # side selection
    # ------------------------------------------------------------------ #
    def _vote_side(self, lm: list[list[float]]) -> None:
        for side, idx in SIDE_INDICES.items():
            vis = sum(_visibility(lm, idx[j]) for j in self.cfg.triple) / 3.0
            self._side_votes[side] += vis
        if self._frames_seen >= self.SIDE_LOCK_FRAMES:
            self.side = max(self._side_votes, key=lambda k: self._side_votes[k])
            self._side_locked = True

    # ------------------------------------------------------------------ #
    # main entry point
    # ------------------------------------------------------------------ #
    def update(
        self,
        t: float,
        lm: list[list[float]],
        wlm: list[list[float]] | None = None,
    ) -> FrameResult:
        self._frames_seen += 1
        if not lm or len(lm) < 33:
            return FrameResult(t, {}, self.state, 0.0, self.rep_count, 0.0,
                               ["no_pose"], ok=False, message="No person detected")

        if self.first_ts is None:
            self.first_ts = t
        self.last_ts = t

        if not self._side_locked:
            self._vote_side(lm)

        idx = SIDE_INDICES[self.side]
        geo = wlm if (wlm and len(wlm) >= 33) else lm   # metric if available

        # ---- primary driving angle ----
        a, b, c = (geo[idx[j]] for j in self.cfg.triple)
        raw_angle = angle_deg(a, b, c)
        angle = self.smoother.push(raw_angle)

        # ---- secondary angles, always reported so the UI can show them ----
        angles = {self.cfg.joint: angle}
        angles["elbow"] = angle_deg(geo[idx["shoulder"]], geo[idx["elbow"]], geo[idx["wrist"]])
        angles["knee"] = angle_deg(geo[idx["hip"]], geo[idx["knee"]], geo[idx["ankle"]])
        angles["hip"] = angle_deg(geo[idx["shoulder"]], geo[idx["hip"]], geo[idx["knee"]])
        angles["shoulder_abduction"] = angle_deg(
            geo[idx["hip"]], geo[idx["shoulder"]], geo[idx["elbow"]]
        )
        angles["torso_lean"] = angle_from_vertical(lm[idx["shoulder"]], lm[idx["hip"]])
        angles[self.cfg.joint] = angle  # keep the smoothed value authoritative

        # ---- tracking quality ----
        vis = sum(_visibility(lm, idx[j]) for j in self.cfg.triple) / 3.0
        self._vis_sum += vis
        self._vis_n += 1

        live_flags: list[str] = []
        if vis < self.MIN_TRACK_VIS:
            live_flags.append("low_visibility")

        # ---- live form rules (evaluated every frame, aggregated per rep) ----
        frame_flags, frame_penalty = self._form_rules(lm, geo, idx, angles)
        live_flags.extend(frame_flags)

        # ---- finite state machine ----
        rep_event = self._advance_fsm(t, angle, vis, frame_flags, frame_penalty)

        progress = self._progress(angle)

        self.frames.append({
            "t": round(t, 3),
            "angle": round(angle, 2),
            "state": self.state,
            "vis": round(vis, 3),
        })
        if len(self.raw_frames) < MAX_RAW_FRAMES:
            self.raw_frames.append({"t": round(t, 3), "lm": lm, "wlm": wlm})
        else:
            self.raw_dropped += 1

        return FrameResult(
            t=t, angles=angles, state=self.state, progress=progress,
            rep_count=self.rep_count, visibility=vis,
            live_flags=sorted(set(live_flags)), rep_event=rep_event,
            ok=vis >= self.MIN_TRACK_VIS,
            message="" if vis >= self.MIN_TRACK_VIS else "Step back so the joint is fully visible",
        )

    # ------------------------------------------------------------------ #
    def _progress(self, angle: float) -> float:
        lo, hi = sorted((self.cfg.start_thresh, self.cfg.peak_thresh))
        p = (angle - lo) / (hi - lo) if hi > lo else 0.0
        p = max(0.0, min(1.0, p))
        return p if self.cfg.direction == "increase" else 1.0 - p

    def _at_peak(self, angle: float) -> bool:
        return (angle <= self.cfg.peak_thresh if self.cfg.direction == "decrease"
                else angle >= self.cfg.peak_thresh)

    def _at_start(self, angle: float) -> bool:
        return (angle >= self.cfg.start_thresh if self.cfg.direction == "decrease"
                else angle <= self.cfg.start_thresh)

    def _advance_fsm(
        self,
        t: float,
        angle: float,
        vis: float,
        frame_flags: list[str],
        frame_penalty: float,
    ) -> RepEvent | None:
        """
        Two-state machine with hysteresis.

          start --(angle crosses peak_thresh)--> peak
          peak  --(angle returns past start_thresh)--> start   ==> ONE REP

        The gap between start_thresh and peak_thresh is the hysteresis band.
        Without it, a hand trembling on the threshold counts twenty reps a
        second - the single most common bug in webcam rep counters.
        """
        # accumulate while inside a rep
        if self._rep_start_ts is not None:
            self._rep_min = min(self._rep_min, angle)
            self._rep_max = max(self._rep_max, angle)
            self._rep_vis.append(vis)
            self._rep_flags.update(frame_flags)
            self._rep_penalty += frame_penalty

        if self.state == "start":
            if self._at_peak(angle):
                self.state = "peak"
                if self._rep_start_ts is None:
                    self._rep_start_ts = t
                    self._rep_min = angle
                    self._rep_max = angle
                    self._rep_vis = [vis]
                    self._rep_flags = set(frame_flags)
                    self._rep_penalty = frame_penalty
            elif self._rep_start_ts is None:
                # remember when the descent began so tempo covers the whole rep
                self._rep_start_ts = t
                self._rep_min = angle
                self._rep_max = angle
                self._rep_vis = [vis]
                self._rep_flags = set()
                self._rep_penalty = 0.0
            return None

        # state == "peak"
        if self._at_start(angle):
            start_ts = self._rep_start_ts if self._rep_start_ts is not None else t
            tempo = t - start_ts
            self.state = "start"

            if tempo < self.cfg.min_rep_s:
                self._reset_rep(t, angle, vis)
                return None                      # bounce / jitter, not a rep
            if tempo > self.cfg.max_rep_s:
                self._reset_rep(t, angle, vis)
                return None                      # user wandered off mid-rep

            rom = abs(self._rep_max - self._rep_min)
            flags = set(self._rep_flags)
            penalty = min(0.55, self._rep_penalty / max(1, len(self._rep_vis)))

            if rom < self.cfg.full_rom_deg * 0.70:
                flags.add("partial_rom")
                penalty += 0.22
            if tempo < 1.0:
                flags.add("too_fast")
                penalty += 0.15

            mean_vis = sum(self._rep_vis) / max(1, len(self._rep_vis))
            form = max(0.0, min(1.0, 1.0 - penalty))

            self.rep_count += 1
            ev = RepEvent(
                index=self.rep_count,
                start_ts=start_ts,
                end_ts=t,
                tempo_s=round(tempo, 3),
                rom_deg=round(rom, 1),
                peak_angle=round(self._rep_max, 1),
                min_angle=round(self._rep_min, 1),
                form_score=round(form, 3),
                flags=sorted(flags),
                mean_visibility=round(mean_vis, 3),
            )
            self.reps.append(ev)
            self._reset_rep(t, angle, vis)
            return ev
        return None

    def _reset_rep(self, t: float, angle: float, vis: float) -> None:
        self._rep_start_ts = t
        self._rep_min = angle
        self._rep_max = angle
        self._rep_vis = [vis]
        self._rep_flags = set()
        self._rep_penalty = 0.0

    # ------------------------------------------------------------------ #
    # form rules
    # ------------------------------------------------------------------ #
    def _form_rules(
        self,
        lm: list[list[float]],
        geo: list[list[float]],
        idx: dict[str, int],
        angles: dict[str, float],
    ) -> tuple[list[str], float]:
        """
        Cheap per-frame geometric checks. Each returns a flag plus a small
        penalty; penalties are averaged over the rep so one noisy frame
        cannot destroy a rep score.
        """
        flags: list[str] = []
        penalty = 0.0
        ex = self.cfg.key

        torso_lean = angles["torso_lean"]

        if ex in ("bicep_curl", "shoulder_press", "lateral_raise"):
            if torso_lean > 18.0:
                flags.append("torso_swing")
                penalty += 0.30
            # Elbow should stay under the shoulder, not drift forward/back.
            elbow_drift = abs(lm[idx["elbow"]][0] - lm[idx["hip"]][0])
            shoulder_w = max(1e-3, abs(lm[L_SHOULDER][0] - lm[R_SHOULDER][0]))
            if ex == "bicep_curl" and elbow_drift > 1.15 * shoulder_w:
                flags.append("elbow_drift")
                penalty += 0.25

        if ex == "squat":
            if torso_lean > 45.0:
                flags.append("excessive_forward_lean")
                penalty += 0.30
            # Knee valgus: knee tracking inside the ankle in image x.
            knee_x, ankle_x, hip_x = (lm[idx["knee"]][0], lm[idx["ankle"]][0], lm[idx["hip"]][0])
            inward = (knee_x - ankle_x) * (1.0 if hip_x > ankle_x else -1.0)
            if inward < -0.045:
                flags.append("knee_valgus")
                penalty += 0.30
            if angles["knee"] > 150.0 and self.state == "peak":
                flags.append("shallow_depth")
                penalty += 0.20

        if ex == "pushup":
            body_line = angle_deg(geo[idx["shoulder"]], geo[idx["hip"]], geo[idx["knee"]])
            if body_line < 160.0:
                flags.append("hip_sag")
                penalty += 0.30

        if ex == "shoulder_press":
            if angles["elbow"] > 175.0:
                flags.append("elbow_lockout")
                penalty += 0.10

        return flags, penalty

    # ------------------------------------------------------------------ #
    def summary(self) -> dict[str, Any]:
        dur = (self.last_ts - self.first_ts) if (self.first_ts and self.last_ts) else 0.0
        scores = [r.form_score for r in self.reps]
        return {
            "exercise": self.cfg.key,
            "exercise_name": self.cfg.name,
            "side": self.side,
            "reps": self.rep_count,
            "duration_s": round(dur, 2),
            "avg_form_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
            "mean_visibility": round(self._vis_sum / self._vis_n, 3) if self._vis_n else 0.0,
            "frames": len(self.frames),
            "raw_frames": len(self.raw_frames),
            "raw_dropped": self.raw_dropped,
            "rep_times": [round(r.end_ts, 3) for r in self.reps],
            "flag_counts": _count_flags(self.reps),
            "est_kcal": round(self.cfg.mets * 3.5 * 70 / 200 * (dur / 60.0), 1),
        }


def _visibility(lm: list[list[float]], i: int) -> float:
    try:
        pt = lm[i]
        return float(pt[3]) if len(pt) > 3 else 1.0
    except (IndexError, TypeError, ValueError):
        return 0.0


def _count_flags(reps: list[RepEvent]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in reps:
        for f in r.flags:
            out[f] = out.get(f, 0) + 1
    return out


def list_exercises() -> list[dict[str, Any]]:
    return [
        {
            "key": c.key,
            "name": c.name,
            "joint": c.joint,
            "start_thresh": c.start_thresh,
            "peak_thresh": c.peak_thresh,
            "direction": c.direction,
        }
        for c in EXERCISES.values()
    ]
