# Demo script and viva preparation

## Before you walk in

- [ ] Open the deployed site **2 minutes early** so the free dyno is awake.
- [ ] Have `backend/eval_out/results.md` open in a second tab.
- [ ] Have a fallback: the whole thing runs locally with SQLite and no network.
      If the venue wifi dies, run `uvicorn` + `npm run dev` on your laptop.
- [ ] Charge the laptop. The GPU delegate falls back to CPU on battery saver
      and the frame rate halves.
- [ ] Stand side-on to the camera with your whole arm in frame. Good light.
- [ ] If you have the ESP32: power it, then check `/api/imu/status` shows
      `samples_seen` climbing **before** you start talking.

---

## The 5-minute demo

**1. The claim (30 s).** "A webcam rep counter fails on occlusion. An IMU on the
equipment cannot tell a rep from a knock. They fail in different ways, so
combining them beats either. That is what I measured."

**2. A clean set (60 s).** Workout tab → Start set → 8 bicep curls at a steady
tempo. Point at the three counters agreeing. Point at the live form flags.

**3. Break the camera (60 s).** Start a set. Halfway through, step partly behind
a chair or hold a bag in front of your arm. The camera counter stalls; the
visibility percentage drops. Press **Simulate IMU** first so the IMU is
streaming — the fused counter keeps going and the rep is labelled
*"camera occluded, IMU observed motion"*.

**4. Break the IMU (45 s).** With the arm clearly visible, the simulator's
injected knocks produce IMU events with no matching camera rep. Open the session
afterwards and show those rows marked **rejected**, reason *"camera had a clear
view and saw no rep"*.

**5. The sensitivity slider (45 s).** Open the finished session → drag the match
window → **Re-run fusion**. This re-runs the real fusion algorithm on stored
data. Say: *"this is my parameter sweep, on real recorded data, live."*

**6. The results table (60 s).** Show `results.md`. Give the honest headline:
worst-case F1 0.710 / 0.785 / **0.948**, and *which sensor wins changes with the
condition*, so you cannot pick one in advance.

**7. The rest, briefly (30 s).** Diet tab — Mifflin-St Jeor, cite the 1990
paper, note the solver lands within ~7% on every macro. Habits tab — skip-risk
with per-feature explanations, and state up front that it is trained on
synthetic data.

---

## Questions you will be asked

**"Why not just use a better pose model?"**
A better model reduces jitter; it does not see through a chair. Occlusion is a
geometric problem, not an accuracy problem. That is condition C2, where the
camera's recall drops to 0.55 while the IMU stays at 1.00.

**"Why not put the rep counter on the ESP32?"**
Then the raw signal is thrown away and you can never re-analyse it. The whole
evaluation depends on replaying archived raw data with different thresholds.
Also, form assessment needs the camera anyway.

**"Isn't your evaluation just simulated?"**
The controlled table is, and it is labelled as such. It exists to isolate *one*
failure mode per cell, which you cannot do repeatably with a human. The real-
session table is Table 3. *(Have real sessions in it. If you do not, say
exactly that and give the number you do have — never imply simulation is
validation.)*

**"How do you know the two clocks line up?"**
I do not synchronise them. I estimate the constant offset by searching for the
shift that maximises agreement between the two event trains, over ±2.5 s at
20 ms steps. Measured recovery error: 268 ms mean, 150 ms median, and the
residual is the known difference in detector latency between the two modalities.

**"What happens if the IMU is not attached at all?"**
`detect_imu_reps` has an absolute activity floor — 8 °/s on the gyro. An idle
sensor returns zero reps rather than noise-derived ones. Fusion then accepts the
camera reps on their own, which is the correct behaviour for bodyweight work.

**"Why greedy matching and not the Hungarian algorithm?"**
Rep events are monotonic in time and well separated, so the greedy and optimal
assignments coincide in practice. Greedy is O(nm log nm) and I can explain it
here at the board.

**"Your form scores — validated against what?"**
Nothing clinical. They are geometric heuristics with plausible thresholds. It is
limitation 3 in my report. Validating them needs a physiotherapist rating
sessions, which is future work.

**"Why is the fused count sometimes lower than both sensors?"**
Because fusion can reject. If the camera hallucinated a rep during a fidget and
the IMU never moved, neither sensor's raw count is right and the fused count is
lower than the camera's. Condition C3 shows exactly this.

---

## What to have on screen if something breaks

1. `eval_out/fig_f1_by_condition.png` — the one chart that carries the claim.
2. `eval_out/results.md` — the numbers.
3. `backend/app/engines/fusion_engine.py` — the `fuse()` docstring states the
   decision policy in plain English. Read it aloud if the demo dies.

A failed live demo with a good results table is a pass. A great demo with no
numbers is not.
