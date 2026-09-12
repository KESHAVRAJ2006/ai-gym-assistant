"""
End-to-end smoke tests.

Run:
    cd backend
    .venv\\Scripts\\python.exe -m pytest -v

These are deliberately integration-level rather than unit tests: the thing
that breaks in a project like this is the wiring between layers, not the
arithmetic inside one function. Each test exercises a full request path
through FastAPI, SQLAlchemy and a real (temporary) database.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

# Point the app at a throwaway SQLite file BEFORE app modules import settings.
_TMP = Path(tempfile.gettempdir()) / f"aigym_test_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}"
os.environ["MONGO_URL"] = ""
os.environ["MQTT_HOST"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["JWT_SECRET"] = "test-secret-not-used-in-production"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    engine.dispose()
    _TMP.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def auth(client):
    email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={
        "email": email, "password": "hunter2pass", "full_name": "Test Athlete",
        "age": 21, "sex": "male", "height_cm": 175, "weight_kg": 70,
        "activity_level": "moderate", "goal": "bulk",
    })
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, email


# ------------------------------------------------------------------ meta --
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"]["ok"] is True


def test_exercises(client):
    r = client.get("/api/exercises")
    assert r.status_code == 200
    keys = {e["key"] for e in r.json()}
    assert {"bicep_curl", "squat", "pushup"} <= keys


# ------------------------------------------------------------------ auth --
def test_duplicate_registration_rejected(client, auth):
    _, email = auth
    r = client.post("/api/auth/register",
                    json={"email": email, "password": "another-pass"})
    assert r.status_code == 409


def test_login_and_me(client, auth):
    headers, email = auth
    r = client.post("/api/auth/login", json={"email": email, "password": "hunter2pass"})
    assert r.status_code == 200
    r = client.post("/api/auth/login", json={"email": email, "password": "WRONG"})
    assert r.status_code == 401
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200 and r.json()["full_name"] == "Test Athlete"


def test_protected_route_requires_token(client):
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/sessions",
                      headers={"Authorization": "Bearer garbage"}).status_code == 401


# ------------------------------------------------------------------ diet --
def test_diet_plan_hits_its_own_targets(client, auth):
    headers, _ = auth
    r = client.post("/api/diet/generate", headers=headers,
                    json={"diet_type": "veg", "meals_per_day": 4})
    assert r.status_code == 200, r.text
    plan = r.json()

    t, a = plan["targets"], plan["achieved"]
    assert t["bmr"] > 1200
    assert t["tdee"] > t["bmr"]
    assert t["target_kcal"] > t["tdee"]           # goal is bulk

    # The coordinate-descent solver should land within 10% on every macro.
    for macro in ("protein_g", "carbs_g", "fat_g", "target_kcal"):
        rel = abs(a[macro] - t[macro]) / t[macro]
        assert rel < 0.10, f"{macro} off by {rel:.1%} (target {t[macro]}, got {a[macro]})"
    assert len(plan["meals"]) == 4
    assert all(m["items"] for m in plan["meals"])

    assert client.get("/api/diet/latest", headers=headers).json()["id"] == plan["id"]


def test_mifflin_st_jeor_matches_published_equation():
    from app.engines.diet_engine import mifflin_st_jeor

    # 10*80 + 6.25*180 - 5*30 + 5 = 800 + 1125 - 150 + 5
    assert mifflin_st_jeor(80, 180, 30, "male") == pytest.approx(1780.0)
    # ... - 161 for the female coefficient
    assert mifflin_st_jeor(80, 180, 30, "female") == pytest.approx(1614.0)


# ---------------------------------------------------------------- habits --
def test_habits_and_skip_risk(client, auth):
    headers, _ = auth
    assert client.post("/api/habits/seed-demo?days=40",
                       headers=headers).status_code == 200
    logs = client.get("/api/habits", headers=headers).json()
    assert len(logs) >= 30

    r = client.get("/api/habits/skip-risk?sleep_h=4.5&soreness=5&mood=1",
                   headers=headers)
    assert r.status_code == 200, r.text
    bad = r.json()
    r = client.get("/api/habits/skip-risk?sleep_h=8.5&soreness=0&mood=5",
                   headers=headers)
    good = r.json()

    assert 0.0 <= bad["skip_probability"] <= 1.0
    # The model must at minimum order these two obvious cases correctly.
    assert bad["skip_probability"] > good["skip_probability"]
    assert bad["risk_band"] in {"low", "medium", "high"}

    streak = client.get("/api/habits/streak", headers=headers).json()
    assert streak["days_logged"] >= 30


def test_habit_model_metrics_are_real(client):
    m = client.get("/api/habits/model-metrics").json()
    assert m["roc_auc"] > 0.7, "model should beat chance on its own synthetic data"
    assert "synthetic" in m["data_source"]


# ------------------------------------------------------------------ chat --
def test_chat_falls_back_to_rules(client, auth):
    headers, _ = auth
    r = client.post("/api/chat", headers=headers, json={"message": "how much protein?"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "rules"          # no API key set in tests
    assert "protein" in body["reply"].lower()


def test_chat_defers_medical_questions(client, auth):
    headers, _ = auth
    r = client.post("/api/chat", headers=headers,
                    json={"message": "my shoulder has sharp pain, should I push through?"})
    assert "clinician" in r.json()["reply"].lower()


# ------------------------------------------------------------------- IMU --
def test_imu_http_ingest(client):
    r = client.post("/api/imu/ingest", json={
        "device_id": "test-dev", "t": 1.0,
        "ax": 0.0, "ay": 0.0, "az": 9.8, "gx": 1.0, "gy": 2.0, "gz": 3.0,
    })
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/api/imu/status").json()["bus"]["samples_seen"] >= 1


# ------------------------------------------------------- websocket + fusion --
def test_websocket_workout_counts_reps(client, auth):
    """
    Drive the live WebSocket with simulated landmarks and assert the session
    is counted, fused and persisted. This is the single most important test
    in the file - it is the whole camera path end to end.
    """
    from app.eval.simulator import Condition, simulate

    headers, _ = auth
    token = headers["Authorization"].split()[1]
    sim = simulate(Condition("ws_test", n_reps=6, period_s=2.4), seed=3)

    with client.websocket_connect(f"/ws/workout?token={token}") as ws:
        assert ws.receive_json()["type"] == "ready"

        ws.send_json({"type": "start", "exercise": "bicep_curl",
                      "device_id": "test-dev-ws"})
        started = ws.receive_json()
        assert started["type"] == "started"
        session_id = started["session_id"]

        reps_seen = 0
        for f in sim.cam_frames:
            ws.send_json({"type": "frame", "t": f["t"], "lm": f["lm"], "wlm": f["wlm"]})
            msg = ws.receive_json()
            assert msg["type"] == "angles"
            if "rep" in msg:
                reps_seen += 1
                assert ws.receive_json()["type"] == "rep"

        ws.send_json({"type": "stop"})
        summary = ws.receive_json()

    assert summary["type"] == "summary"
    assert summary["reps"] == 6, f"expected 6 reps, engine counted {summary['reps']}"
    assert reps_seen == 6
    assert 0.0 <= summary["avg_form_score"] <= 1.0

    detail = client.get(f"/api/sessions/{session_id}", headers=headers).json()
    assert detail["camera_reps"] == 6
    assert detail["fused_reps"] == 6
    assert len([r for r in detail["reps"] if r["source"] == "camera"]) == 6

    patched = client.patch(f"/api/sessions/{session_id}", headers=headers,
                           json={"ground_truth_reps": 6})
    assert patched.json()["ground_truth_reps"] == 6

    summary_ep = client.get("/api/sessions/summary", headers=headers).json()
    assert summary_ep["total_sessions"] >= 1
    assert summary_ep["accuracy"]["fused_mae"] == 0.0


def test_websocket_rejects_bad_token(client):
    with client.websocket_connect("/ws/workout?token=not-a-jwt") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "error"


# ---------------------------------------------------------------- fusion --
def test_fusion_beats_camera_under_occlusion():
    """The core claim, as an assertion the test suite will not let you break."""
    from app.eval.simulator import Condition, run_pose_engine, simulate
    from app.engines.fusion_engine import detect_imu_reps, evaluate, fuse

    cond = Condition("occluded", n_reps=12, occlusion_frac=0.4, clock_offset_s=0.7)
    sim = simulate(cond, seed=11)
    pose = run_pose_engine(sim)
    imu = detect_imu_reps(sim.imu_samples)
    res = fuse(pose["reps"], imu, visibility_timeline=pose["visibility_timeline"])

    cam_f1 = evaluate(pose["times"], sim.gt_times, allow_offset=True)["f1"]
    fus_f1 = evaluate([r.t for r in res.reps if r.accepted], sim.gt_times,
                      allow_offset=True)["f1"]
    assert fus_f1 > cam_f1, f"fusion {fus_f1} did not beat camera {cam_f1}"


def test_clock_offset_is_recovered():
    from app.eval.simulator import Condition, run_pose_engine, simulate
    from app.engines.fusion_engine import detect_imu_reps, fuse

    for true_off in (-1.5, 0.0, 1.2):
        sim = simulate(Condition("off", n_reps=10, clock_offset_s=true_off), seed=5)
        pose = run_pose_engine(sim)
        res = fuse(pose["reps"], detect_imu_reps(sim.imu_samples),
                   visibility_timeline=pose["visibility_timeline"])
        # Recovered offset should be -true_off, within detector latency.
        assert abs(res.offset_s - (-true_off)) < 0.45, (
            f"true {-true_off}, estimated {res.offset_s}")


def test_fusion_rejects_imu_bumps_the_camera_never_saw():
    """
    Whether a given knock trips the IMU detector is seed-dependent, so this
    sweeps seeds: it asserts the invariant on every seed, and separately
    asserts that at least one seed actually produced the over-count the test
    is about - otherwise a detector that stopped detecting anything would
    pass silently.
    """
    from app.eval.simulator import Condition, run_pose_engine, simulate
    from app.engines.fusion_engine import detect_imu_reps, fuse

    n_reps = 10
    saw_overcount = False
    for seed in range(8):
        sim = simulate(Condition("bumps", n_reps=n_reps, bumps=5), seed=seed)
        pose = run_pose_engine(sim)
        imu = detect_imu_reps(sim.imu_samples)
        res = fuse(pose["reps"], imu,
                   visibility_timeline=pose["visibility_timeline"])
        if len(imu) > n_reps:
            saw_overcount = True
        # The invariant: fusion is never further from the truth than the IMU.
        assert abs(res.fused_count - n_reps) <= abs(len(imu) - n_reps), (
            f"seed {seed}: imu {len(imu)}, fused {res.fused_count}, gt {n_reps}")
        assert res.fused_count == n_reps, f"seed {seed}: fused {res.fused_count}"

    assert saw_overcount, "no seed produced an IMU over-count - test is vacuous"


# ----------------------------------------------------------------- admin --
def test_admin_overview_is_reachable_by_first_user(client, auth):
    """
    With ADMIN_EMAILS unset the first registered account is the admin. The
    fixture user is id 1 in this throwaway database, so it qualifies.
    """
    headers, _ = auth
    r = client.get("/api/admin/overview", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["users"] >= 1
    assert "accuracy" in body
    assert isinstance(body["users"], list)
    assert body["users"][0]["email"]


def test_admin_system_reports_subsystems(client, auth):
    headers, _ = auth
    body = client.get("/api/admin/system", headers=headers).json()
    assert body["database"]["ok"] is True
    assert "mongo" in body and "mqtt" in body
    assert body["fusion_defaults"]["match_window_s"] > 0


def test_admin_model_card_carries_the_synthetic_warning(client, auth):
    headers, _ = auth
    body = client.get("/api/admin/model-card", headers=headers).json()
    assert body["roc_auc"] > 0.7
    assert "SYNTHETIC" in body["warning"]
    assert len(body["features"]) == 9


def test_admin_is_refused_for_a_non_admin_user(client):
    """A second account must not be able to read system-wide analytics."""
    r = client.post("/api/auth/register", json={
        "email": f"normal_{uuid.uuid4().hex[:8]}@example.com",
        "password": "hunter2pass",
    })
    assert r.status_code == 201
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}

    assert r.json()["user"]["is_admin"] is False
    for path in ("/api/admin/overview", "/api/admin/system", "/api/admin/model-card"):
        assert client.get(path, headers=other).status_code == 403, path


def test_admin_endpoints_require_authentication(client):
    assert client.get("/api/admin/overview").status_code == 401
