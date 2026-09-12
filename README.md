# AI Gym & Fitness Assistant

A webcam + IMU system that counts repetitions, validates form, and wraps that in
a diet planner and a habit tracker.

**Academic contribution: camera + IMU sensor fusion for reliable repetition
counting and form validation.** Everything else in this repository exists to make
that contribution usable and demonstrable. If a feature does not strengthen that
claim, it is scaffolding.

---

## The claim, and the numbers behind it

Measured by `backend/scripts/run_evaluation.py` over 6 conditions x 20 seeds,
running the real `PoseEngine` and the real `fusion_engine` on synthetic sensor
data with known ground truth:

| Metric | Camera only | IMU only | **Fused** |
|---|---:|---:|---:|
| Mean F1 | 0.920 | 0.933 | **0.988** |
| Worst-case F1 | 0.710 | 0.785 | **0.948** |
| Mean abs. count error (reps/session) | 1.51 | 1.07 | **0.27** |

Clock offset between the two devices is recovered to a mean absolute error of
**268 ms** (median 150 ms) with no synchronisation protocol.

The honest framing, which the report must use: in 4 of 6 conditions one sensor is
already near-perfect, so *mean* F1 flatters nobody. The real argument is the
**floor** — fusion raises worst-case F1 by +0.163 over the better single sensor —
and the fact that **which sensor is better changes with the condition**, so no
system committed to one sensor in advance can match it.

> These numbers come from **simulated** sensor data with controlled degradations.
> That establishes *why* and *when* fusion helps. It is **not** a validation on
> human data. Record real sessions, label them, export them, and re-run the
> script — see [Producing the real results table](#producing-the-real-results-table).

---

## Architecture

```
Browser (React + MediaPipe Tasks JS)
   │  33 pose landmarks per frame — never video
   ▼
FastAPI gateway  (REST + WebSocket, JWT auth)
   ├── pose_engine     joint angles, rep FSM, form rules
   ├── fusion_engine   clock alignment, event matching, accept/reject  ← the contribution
   ├── diet_engine     Mifflin-St Jeor, macro split, meal solver
   ├── habit_model     XGBoost skip-risk on tabular features
   └── chat_service    coaching replies (rules, or Claude if a key is set)
   │
   ├── PostgreSQL  users, sessions, reps, diet plans
   └── MongoDB     raw landmark + IMU time-series (optional)
   ▲
   │  MQTT
ESP32 + MPU6050
```

**Why landmarks and not video.** 33 points at 20 Hz is roughly 8 kB/s. 720p video
is about 400x that. It also means the user's gym footage never leaves their
device — a privacy property worth one paragraph in the report.

**Why fusion works.** The two sensors fail in disjoint ways. A camera loses reps
to occlusion and invents them from landmark jitter. An IMU cannot tell a rep from
someone knocking the dumbbell, and slips when the strap loosens. Disjoint failure
modes are exactly the condition under which fusion beats either sensor alone.

---

## Quick start (local, ~10 minutes)

Prerequisites: Python 3.11+, Node 18+, git. No database setup needed — the
backend falls back to a local SQLite file.

### 1. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # Windows
# source .venv/bin/activate && pip install -r requirements-dev.txt  # macOS/Linux

.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Optional — connect the raw-signal archive (MongoDB Atlas free tier):

```bash
.venv\Scripts\python.exe scripts/setup_mongo.py
```

Paste your Atlas connection string at the hidden prompt; it validates,
connects, checks write permission, and saves `MONGO_URL` to `backend/.env`.

**Verify:** open <http://127.0.0.1:8000/health>. You should see
`"status":"ok"` and `"database":{"ok":true,...}`. Mongo and MQTT reporting
`disabled` is correct and expected — they are optional.

Interactive API docs: <http://127.0.0.1:8000/docs>

### 2. Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

**Verify:** open <http://localhost:5173>, create an account, go to **Workout**,
press **Enable camera**, then **Start set**. Do some bicep curls side-on to the
camera. The rep counter should climb and the skeleton should track your arm.

`npm install` also downloads the MediaPipe model (~5 MB) into
`frontend/public/mediapipe/` so the app works with the network unplugged. Your
viva will be on college wifi.

### 3. See the fusion actually do something

With a set running, press **Simulate IMU**. It streams a synthetic MPU6050
through the *same* code path as the real ESP32, including two deliberate knocks
and a 10% strap-slip rate. Watch the three counters diverge and the fused count
stay correct.

---

## Running the evaluation (phase 9 — the most important deliverable)

```bash
cd backend
.venv\Scripts\python.exe scripts/run_evaluation.py --trials 20
```

Takes about a minute. Writes to `backend/eval_out/`:

| File | What it is |
|---|---|
| `results.md` | Paste-ready tables and the computed headline paragraph |
| `summary.csv` | Micro-averaged metrics per condition x modality |
| `per_trial.csv` | One row per condition x seed x modality |
| `window_sweep.csv` | Sensitivity to the match-window parameter |
| `fig_*.png` | Four figures, colour-blind-safe, ready for the report |

The six conditions isolate one failure mode each: C1 clean, C2 40% of reps
occluded, C3 camera sees rest-position fidgets, C4 IMU strap slips, C5 equipment
knocked, C6 all of it at realistic severity.

### Producing the real results table

`results.md` prints Table 3 as **EMPTY** until you give it real sessions. That is
deliberate — it will not let you quietly report simulation as validation.

1. Record sessions in the web app (vary the conditions: stand partly behind a
   chair, let someone bump the weight, wear the strap loosely).
2. Label each one with the rep count you actually did — the **Ground truth**
   box on the session page.
3. Export and re-run:

```bash
.venv\Scripts\python.exe scripts/export_session.py --list
.venv\Scripts\python.exe scripts/export_session.py --all
.venv\Scripts\python.exe scripts/run_evaluation.py --trials 20
```

Per-rep precision/recall on real data needs the raw landmark archive, which
needs MongoDB. Without it you still get count error, which is enough for a
respectable table — just say which you have.

---

## Admin dashboard

Sign in as the **first registered account** (or set `ADMIN_EMAILS` on the
backend) and an **Admin** tab appears in the navigation. It shows system-wide
analytics rather than one user's:

- platform totals (users, sessions, reps, diet plans, habit logs)
- **counting accuracy across every labelled session from every user** - the
  system-level evidence for the fusion claim
- activity over time and volume by exercise
- the most common form faults across all recorded repetitions
- a user table with per-user session, rep and form-score counts
- live service health (database, MongoDB, MQTT, IMU bus) and the active
  fusion thresholds
- the habit model's card, carrying its own "synthetic data" warning

Admin status is decided from configuration, not a database column, so there
is no row a user can write to in order to promote themselves.

## Tests

```bash
cd backend
.venv\Scripts\python.exe -m pytest -v
```

22 integration tests, ~10 seconds. To regenerate
[`docs/TESTING_REPORT.md`](docs/TESTING_REPORT.md) from a real run, with
coverage:

```bash
.venv\Scripts\python.exe scripts/generate_test_report.py
``` They drive real HTTP and WebSocket requests
against a temporary database. Three of them assert the core claim directly:
fusion beats camera under occlusion, the clock offset is recovered, and IMU
bumps the camera never saw are rejected.

---

## Hardware

Wiring, libraries, and the common failure modes are documented at the top of
[`firmware/esp32_mpu6050/esp32_mpu6050.ino`](firmware/esp32_mpu6050/esp32_mpu6050.ino).
Short version: MPU6050 on 3V3 (not 5V), SDA→GPIO21, SCL→GPIO22, AD0→GND.

Rep detection runs on the **server**, not the ESP32, so that archived raw signal
can be re-analysed with different thresholds later. Firmware that only reports
"rep!" throws away the evidence the report needs.

No hardware? `POST /api/imu/simulate` and the **Simulate IMU** button feed the
identical bus. Nothing downstream can tell the difference.

---

## Deployment

See **[docs/DEPLOY.md](docs/DEPLOY.md)** for the full Render walkthrough,
including the free-tier traps (services sleep after 15 minutes; free Postgres is
deleted after 30 days).

---

## Repository layout

```
backend/
  app/
    main.py           FastAPI app, lifespan, health
    config.py         every environment variable, in one place
    models.py         SQLAlchemy schema
    engines/          pose, fusion, diet, habit, chat
    routers/          auth, sessions, diet, habits, chat, imu, ws
    services/         mongo, mqtt subscriber, in-process IMU bus
    eval/             simulator + report figures
  scripts/            run_evaluation.py, export_session.py, train_habit_model.py
  tests/              integration tests
frontend/src/
  pages/              Auth, Dashboard, Workout, Sessions, Diet, Habits, Coach
  lib/                api client, MediaPipe wrapper
firmware/             ESP32 + MPU6050 sketch
docs/                 DEPLOY, CITATIONS, DEMO_SCRIPT, TESTING_REPORT
```

---

## Known limitations (put these in the report — examiners ask)

1. **The headline numbers are from simulated sensor data.** Real-session
   validation is the last step and is not done until you do it.
2. **The habit model ships trained on a synthetic population.** Its AUC
   validates the pipeline, not human behaviour. The `/api/habits/model-metrics`
   response says so in its own `data_source` field.
3. **Form scoring is not validated against a physiotherapist.** The rules are
   geometric heuristics with plausible thresholds, not clinical ground truth.
4. **One person at a time**, one exercise per session, and the camera must see
   the working joint side-on.
5. **JWT in `localStorage`** is vulnerable to XSS. An httpOnly cookie would be
   correct for production.
6. **The fusion cadence gate assumes a steady working set.** Deliberately
   irregular tempo (drop sets, rest-pause) will cause it to reject real reps.
