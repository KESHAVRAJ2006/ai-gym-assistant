# Literature log

**Log the citation the same day you adopt the idea.** Reconstructing "where did
this threshold come from?" in week 9 is the single most common way a literature
survey turns into a bad afternoon — and it is the exact problem flagged on your
last project.

Rule: every row in the table below must be traceable to something in the code.
Every non-obvious constant in the code should be traceable to a row here, or
explicitly marked as chosen by you.

---

## Adopted — already in the code

| # | Source | What we took | Where it lives |
|---|---|---|---|
| 1 | Mifflin MD, St Jeor ST, et al. "A new predictive equation for resting energy expenditure in healthy individuals." *Am J Clin Nutr* 51(2):241-7, 1990. | The BMR equation, verbatim, both sex coefficients | `diet_engine.mifflin_st_jeor` |
| 2 | Morton RW, et al. "A systematic review, meta-analysis and meta-regression of the effect of protein supplementation on resistance training-induced gains." *Br J Sports Med* 52(6):376-84, 2018. | 1.6–2.2 g protein/kg/day; we use 2.0 cutting, 1.7 maintaining, 1.8 bulking | `diet_engine.PROTEIN_G_PER_KG` |
| 3 | ICMR-NIN. *Nutrient Requirements for Indians*, 2020. | Per-100 g composition for Indian staples | `diet_engine.FOODS` |
| 4 | Bazarevsky V, Grishchenko I, et al. "BlazePose: On-device Real-time Body Pose Tracking." *CVPR Workshops*, 2020. | The 33-landmark topology and the world-landmark convention | `pose_engine` landmark indices |
| 5 | Google. *MediaPipe Pose Landmarker* task documentation, 2023–. | Browser inference, `VIDEO` running mode, visibility semantics | `frontend/src/lib/pose.js` |
| 6 | Schmitt OH. "A thermionic trigger." *Journal of Scientific Instruments* 15(1):24, 1938. | Hysteresis as a debouncing mechanism — the two-threshold idea used identically for both modalities | `pose_engine._advance_fsm`, `fusion_engine._schmitt_cycles` |

---

## To read and log — gaps you must fill

These are the sections an examiner will probe. Each needs at least two real
citations before submission.

| Topic | Why you need it | Status |
|---|---|---|
| Vision-based rep counting (prior work) | To position `pose_engine` as a baseline, not a contribution | **TODO** |
| IMU-based exercise recognition / counting | Same, for the IMU path | **TODO** |
| Multimodal sensor fusion for HAR (human activity recognition) | This is where your contribution sits; you must show what is already done | **TODO** |
| Early vs late fusion taxonomy | Yours is **late/decision-level** fusion — name it with a citation | **TODO** |
| Time synchronisation of unsynchronised sensors | Justify grid-search offset estimation over cross-correlation or NTP | **TODO** |
| Event-matching metrics for detection tasks | Justify greedy 1-to-1 matching within a tolerance window | **TODO** |
| Exercise form assessment from pose | Positions the form-rule module honestly as heuristic | **TODO** |
| Habit formation / adherence prediction | Supports `habit_model` being more than a toy | **TODO** |

Suggested starting points (verify each before citing — do not cite from this
list alone):
- IEEE Xplore / ACM DL: "repetition counting IMU wearable", "multimodal fusion
  human activity recognition", "exercise form assessment pose estimation".
- *Sensors* (MDPI) has a lot of open-access work on IMU exercise counting.
- Google Scholar the BlazePose paper and read what cites it.

---

## Design choices that are MINE, not from a paper

Be able to say this sentence in the viva: *"that constant is my choice, and
here is the experiment that justifies it."* Claiming a citation you do not have
is worse than admitting you tuned it.

| Choice | Value | Justification available |
|---|---|---|
| Match window between camera and IMU rep events | 0.60 s | Swept 0.15–1.40 s; `eval_out/window_sweep.csv` and `fig_window_sweep.png`. F1 varies by only 0.03 across the whole range — the system is insensitive to it, which is itself the finding |
| Low-visibility threshold for accepting a camera-only rep | 0.55 | Slider on the session page re-runs fusion live; sweepable per session |
| Cadence gate for single-sensor reps | 0.45 x median confirmed inter-rep interval | Mine. Motivated by the C3 condition, where camera false positives land just after a real rep while genuine IMU-missed reps land on the beat |
| Schmitt threshold for the IMU | 0.8 robust sigma | Mine, tuned on the simulator |
| Robust scale = 80th percentile of \|x − median\| rather than MAD | q = 0.80 | Mine, and there is a measured reason: MAD collapses to the noise floor when the signal has a ~50% duty cycle. A gyro axis swinging ±410 °/s reported a MAD of 7 °/s. Documented in `fusion_engine._robust_scale` |
| Absolute activity floors | 8 °/s gyro, 0.35 m/s² accel | Mine. Every other threshold is relative, so an idle sensor would otherwise manufacture reps from its own noise |
| Per-exercise angle thresholds | see `EXERCISES` | Mine, from anatomical range-of-motion norms. **Cite an ROM reference or state clearly that these are empirical.** |
| Form-rule penalties | 0.10–0.30 per flag | Mine, unvalidated. Say so — see limitation 3 in the README |

---

## Citation hygiene

- Pick one style (IEEE is standard for ECE) and use it everywhere.
- Cite the **paper**, not a blog post summarising it.
- If you only read the abstract, either read the rest or do not cite it.
- Keep a BibTeX file next to this one and export from your reference manager.
- Every figure reproduced from another work needs a caption credit **and**
  permission if the thesis will be published.
