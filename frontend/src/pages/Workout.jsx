/**
 * The live workout screen.
 *
 * Pipeline per frame:
 *   webcam -> MediaPipe PoseLandmarker (in this tab, on the GPU)
 *          -> 33 landmarks -> WebSocket -> pose_engine (server)
 *          -> angles + rep events -> back here
 *
 * In parallel the server receives IMU rep events from MQTT (or the simulator)
 * and pushes the fused count down the same socket.
 *
 * Frame pacing: MediaPipe is asked for a result on every animation frame so
 * the skeleton looks smooth, but landmarks are only SENT at SEND_HZ. The
 * server's rep FSM does not get better with more than ~20 Hz, and a phone on
 * college wifi very much notices the difference.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, wsUrl } from "../lib/api.js";
import {
  createPoseLandmarkerSafe,
  drawPose,
  packLandmarks,
  startCamera,
  stopCamera,
} from "../lib/pose.js";
import { Alert, Card, Field, Pill, Stat } from "../components/Ui.jsx";

const SEND_HZ = 20;
const FLAG_TEXT = {
  low_visibility: "Joint not clearly visible",
  partial_rom: "Partial range of motion",
  too_fast: "Too fast - slow the lowering",
  torso_swing: "Torso swinging - brace your core",
  elbow_drift: "Elbow drifting away from your side",
  knee_valgus: "Knees caving inward",
  excessive_forward_lean: "Leaning too far forward",
  shallow_depth: "Not deep enough",
  hip_sag: "Hips sagging - squeeze your glutes",
  elbow_lockout: "Hard lockout - keep tension",
  off_cadence: "Off the set's rhythm",
  camera_occluded: "Counted by IMU while occluded",
  unconfirmed_imu: "IMU event the camera did not see",
};

export default function Workout() {
  const navigate = useNavigate();

  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);
  const landmarkerRef = useRef(null);
  const wsRef = useRef(null);
  const rafRef = useRef(0);
  const lastSendRef = useRef(0);
  const lastVideoTimeRef = useRef(-1);
  const runningRef = useRef(false);

  const [exercises, setExercises] = useState([]);
  const [exercise, setExercise] = useState("bicep_curl");
  const [status, setStatus] = useState("idle"); // idle | loading | ready | live | done
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [modelSource, setModelSource] = useState("");

  const [live, setLive] = useState({
    reps: 0, imu: 0, fused: 0, matched: 0, state: "start",
    progress: 0, visibility: 1, angle: 0, flags: [], message: "",
    offset: 0, cameraOnly: 0, imuOnly: 0,
  });
  const [repFeed, setRepFeed] = useState([]);
  const [summary, setSummary] = useState(null);
  const [sessionId, setSessionId] = useState(null);
  const [gtReps, setGtReps] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.exercises().then(setExercises).catch(() => {});
  }, []);

  // ---------------------------------------------------------------- setup --
  const prepare = useCallback(async () => {
    setErr("");
    setStatus("loading");
    try {
      const { landmarker, source } = await createPoseLandmarkerSafe();
      landmarkerRef.current = landmarker;
      setModelSource(source);
      streamRef.current = await startCamera(videoRef.current);
      setStatus("ready");
    } catch (e) {
      setErr(e.message);
      setStatus("idle");
    }
  }, []);

  // ------------------------------------------------------------ main loop --
  const loop = useCallback(() => {
    if (!runningRef.current) return;
    rafRef.current = requestAnimationFrame(loop);

    const video = videoRef.current;
    const lmk = landmarkerRef.current;
    if (!video || !lmk || video.readyState < 2) return;

    // detectForVideo() rejects a timestamp it has already seen, which is what
    // happens whenever the render loop runs faster than the camera.
    if (video.currentTime === lastVideoTimeRef.current) return;
    lastVideoTimeRef.current = video.currentTime;

    let result;
    try {
      result = lmk.detectForVideo(video, performance.now());
    } catch {
      return;
    }

    drawPose(canvasRef.current, video, result, { state: live.state });

    const now = performance.now();
    if (now - lastSendRef.current < 1000 / SEND_HZ) return;
    lastSendRef.current = now;

    const packed = packLandmarks(result);
    const ws = wsRef.current;
    if (packed && ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "frame", t: Date.now() / 1000, ...packed }));
    }
  }, [live.state]);

  // ---------------------------------------------------------------- start --
  async function start() {
    setErr("");
    setNote("");
    setRepFeed([]);
    setSummary(null);
    setSaved(false);
    setGtReps("");
    setLive((s) => ({ ...s, reps: 0, imu: 0, fused: 0, matched: 0, flags: [] }));

    const ws = new WebSocket(wsUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "start", exercise }));
    };

    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }

      if (msg.type === "started") {
        setSessionId(msg.session_id);
        setStatus("live");
        runningRef.current = true;
        lastVideoTimeRef.current = -1;
        rafRef.current = requestAnimationFrame(loop);
      } else if (msg.type === "angles") {
        setLive((s) => ({
          ...s,
          reps: msg.reps,
          state: msg.state,
          progress: msg.progress,
          visibility: msg.visibility,
          angle: msg.angles?.[Object.keys(msg.angles)[0]] ?? 0,
          flags: msg.flags || [],
          message: msg.message || "",
        }));
      } else if (msg.type === "rep") {
        const f = msg.fusion || {};
        setLive((s) => ({
          ...s,
          fused: f.fused_count ?? s.fused,
          matched: f.matched ?? s.matched,
          offset: f.offset_s ?? s.offset,
          cameraOnly: f.camera_only ?? s.cameraOnly,
          imuOnly: f.imu_only ?? s.imuOnly,
        }));
        setRepFeed((r) => [{ ...msg.rep, src: "camera" }, ...r].slice(0, 40));
      } else if (msg.type === "imu_rep") {
        const f = msg.fusion || {};
        setLive((s) => ({
          ...s,
          imu: msg.imu_count ?? s.imu,
          fused: f.fused_count ?? s.fused,
          matched: f.matched ?? s.matched,
          offset: f.offset_s ?? s.offset,
          imuOnly: f.imu_only ?? s.imuOnly,
        }));
      } else if (msg.type === "summary") {
        setSummary(msg);
        setStatus("done");
      } else if (msg.type === "error") {
        setErr(msg.message);
      }
    };

    ws.onerror = () =>
      setErr("WebSocket error. Check that the backend is running and reachable.");
    ws.onclose = () => {
      runningRef.current = false;
      cancelAnimationFrame(rafRef.current);
      setStatus((s) => (s === "live" ? "ready" : s));
    };
  }

  function stop() {
    runningRef.current = false;
    cancelAnimationFrame(rafRef.current);
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" }));
  }

  async function simulateImu() {
    setNote("");
    try {
      const r = await api.simulateImu({ reps: 10, period_s: 2.6, bumps: 2, miss_prob: 0.1 });
      setNote(
        `Synthetic IMU streaming for ~${r.duration_s}s on device ${r.device_id}. ` +
          `It includes 2 knocks and a 10% strap-slip rate so you can watch the ` +
          `fusion layer reject and rescue reps.`
      );
    } catch (e) {
      setErr(e.message);
    }
  }

  async function saveGroundTruth() {
    if (!sessionId || gtReps === "") return;
    try {
      await api.patchSession(sessionId, { ground_truth_reps: Number(gtReps) });
      setSaved(true);
    } catch (e) {
      setErr(e.message);
    }
  }

  // cleanup on unmount
  useEffect(
    () => () => {
      runningRef.current = false;
      cancelAnimationFrame(rafRef.current);
      try {
        wsRef.current?.close();
      } catch { /* already closed */ }
      stopCamera(streamRef.current);
      landmarkerRef.current?.close?.();
    },
    []
  );

  const cfg = exercises.find((e) => e.key === exercise);

  return (
    <div className="stack">
      <div className="between">
        <h1 style={{ margin: 0 }}>Workout</h1>
        {modelSource && (
          <Pill tone={modelSource.startsWith("local") ? "ok" : "warn"}>
            pose model: {modelSource}
          </Pill>
        )}
      </div>

      <Alert>{err}</Alert>
      <Alert kind="info">{note}</Alert>

      <div className="grid cols-2">
        {/* ------------------------------------------------------- stage -- */}
        <div className="stack">
          <div className="stage">
            <video ref={videoRef} playsInline muted />
            <canvas ref={canvasRef} />
            {status !== "live" && status !== "ready" && (
              <div className="overlay">
                {status === "loading" ? (
                  <span>
                    <span className="spinner" /> Loading the pose model and camera...
                  </span>
                ) : status === "done" ? (
                  <span>Session finished. Scroll down for the summary.</span>
                ) : (
                  <span>
                    Press <strong>Enable camera</strong> to begin. Stand so your whole
                    working arm or leg is in frame, side-on to the camera.
                  </span>
                )}
              </div>
            )}
            {status === "live" && (
              <>
                <div className="hud">
                  <span className="chip" style={{ color: "var(--camera)" }}>
                    camera {live.reps}
                  </span>
                  <span className="chip" style={{ color: "var(--imu)" }}>
                    imu {live.imu}
                  </span>
                  <span className="chip" style={{ color: "var(--fused)" }}>
                    fused {live.fused}
                  </span>
                  <span className="chip">{live.state}</span>
                </div>
                {(live.message || live.flags.length > 0) && (
                  <div className="coach">
                    {live.message ||
                      live.flags.map((f) => FLAG_TEXT[f] || f).join(" | ")}
                  </div>
                )}
              </>
            )}
          </div>

          <div className="gauge" aria-hidden="true">
            <div style={{ width: `${Math.round((live.progress || 0) * 100)}%` }} />
          </div>

          <div className="btn-row">
            {status === "idle" && (
              <button className="btn accent" onClick={prepare}>
                Enable camera
              </button>
            )}
            {(status === "ready" || status === "done") && (
              <button className="btn primary" onClick={start}>
                Start set
              </button>
            )}
            {status === "live" && (
              <button className="btn danger" onClick={stop}>
                Finish set
              </button>
            )}
            <button className="btn" onClick={simulateImu} disabled={status === "idle"}>
              Simulate IMU
            </button>
          </div>
        </div>

        {/* ------------------------------------------------------ panel --- */}
        <div className="stack">
          <Card title="Exercise">
            <Field label="Movement">
              <select
                value={exercise}
                onChange={(e) => setExercise(e.target.value)}
                disabled={status === "live"}
              >
                {exercises.map((e) => (
                  <option key={e.key} value={e.key}>
                    {e.name}
                  </option>
                ))}
              </select>
            </Field>
            {cfg && (
              <p className="hint">
                Counted on the <strong>{cfg.joint.replace("_", " ")}</strong> angle. A rep
                is logged when it passes {cfg.peak_thresh}&deg; and returns past{" "}
                {cfg.start_thresh}&deg;. The gap between those two numbers is the
                hysteresis band that stops a trembling hand counting twenty reps a second.
              </p>
            )}
          </Card>

          <div className="grid cols-3">
            <Stat label="Camera" value={live.reps} tone="camera" />
            <Stat label="IMU" value={live.imu} tone="imu" />
            <Stat label="Fused" value={live.fused} tone="fused" />
          </div>

          <Card title="Fusion state">
            <table>
              <tbody>
                <tr>
                  <td>Confirmed by both sensors</td>
                  <td className="num">{live.matched}</td>
                </tr>
                <tr>
                  <td>Camera only (accepted)</td>
                  <td className="num">{live.cameraOnly}</td>
                </tr>
                <tr>
                  <td>IMU only (accepted)</td>
                  <td className="num">{live.imuOnly}</td>
                </tr>
                <tr>
                  <td>Estimated clock offset</td>
                  <td className="num">{live.offset?.toFixed(2)} s</td>
                </tr>
                <tr>
                  <td>Landmark visibility</td>
                  <td className="num">{(live.visibility * 100).toFixed(0)}%</td>
                </tr>
              </tbody>
            </table>
          </Card>

          <Card title="Reps" hint="Newest first.">
            <div className="rep-feed">
              {repFeed.length === 0 && (
                <p className="muted" style={{ margin: 0 }}>
                  No reps yet.
                </p>
              )}
              {repFeed.map((r, i) => (
                <div className="rep-row" key={`${r.index}-${i}`}>
                  <span className="idx">{r.index}</span>
                  <span className="mono">{r.rom_deg?.toFixed(0)}&deg;</span>
                  <span className="mono muted">{r.tempo_s?.toFixed(1)}s</span>
                  <span
                    className="mono"
                    style={{
                      color:
                        r.form_score >= 0.85
                          ? "var(--fused)"
                          : r.form_score >= 0.6
                            ? "var(--warn)"
                            : "var(--danger)",
                    }}
                  >
                    {(r.form_score * 100).toFixed(0)}
                  </span>
                  <span className="flags">
                    {(r.flags || []).map((f) => (
                      <Pill key={f} tone="warn">
                        {FLAG_TEXT[f] || f}
                      </Pill>
                    ))}
                  </span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>

      {/* ------------------------------------------------------- summary -- */}
      {summary && (
        <Card
          title="Session summary"
          hint="Record how many reps you actually did. Without that label this session contributes nothing to the evaluation table in your report."
        >
          <div className="grid cols-4" style={{ marginBottom: "1rem" }}>
            <Stat label="Camera reps" value={summary.reps} tone="camera" />
            <Stat label="IMU reps" value={summary.fusion?.imu_count ?? 0} tone="imu" />
            <Stat label="Fused reps" value={summary.fusion?.fused_count ?? 0} tone="fused" />
            <Stat
              label="Mean form"
              value={`${Math.round((summary.avg_form_score || 0) * 100)}`}
              sub={`${summary.duration_s}s | ${summary.est_kcal} kcal`}
            />
          </div>

          <div className="row">
            <Field label="Reps you actually counted">
              <input
                type="number"
                min="0"
                value={gtReps}
                onChange={(e) => setGtReps(e.target.value)}
                style={{ width: "9rem" }}
              />
            </Field>
            <button className="btn primary" onClick={saveGroundTruth} disabled={gtReps === ""}>
              Save ground truth
            </button>
            {saved && <Pill tone="ok">saved</Pill>}
            <span className="spacer" />
            <button className="btn" onClick={() => navigate(`/sessions/${sessionId}`)}>
              Open session
            </button>
          </div>
        </Card>
      )}
    </div>
  );
}
