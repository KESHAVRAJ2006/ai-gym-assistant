/**
 * Admin dashboard - system-wide analytics and operations.
 *
 * The distinction from the user Dashboard matters: everything here is
 * aggregated across ALL users. In particular the accuracy panel is the
 * system-level evidence for the project's central claim, computed from every
 * labelled session in the database rather than one person's.
 */
import React, { useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

import { api } from "../lib/api.js";
import { Alert, Card, Empty, Loading, Pill, Stat } from "../components/Ui.jsx";

const AXIS = { stroke: "#6b7784", fontSize: 11 };
const TOOLTIP = {
  contentStyle: {
    background: "#161b22", border: "1px solid #2a313c",
    borderRadius: 8, fontSize: 12, color: "#e6edf3",
  },
};

const FAULT_LABELS = {
  low_visibility: "Joint not clearly visible",
  partial_rom: "Partial range of motion",
  too_fast: "Rep too fast",
  torso_swing: "Torso swinging",
  elbow_drift: "Elbow drifting",
  knee_valgus: "Knees caving inward",
  excessive_forward_lean: "Excessive forward lean",
  shallow_depth: "Insufficient depth",
  hip_sag: "Hips sagging",
  elbow_lockout: "Hard lockout",
};

export default function Admin() {
  const [data, setData] = useState(null);
  const [sys, setSys] = useState(null);
  const [card, setCard] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([
      api.adminOverview(30),
      api.adminSystem().catch(() => null),
      api.adminModelCard().catch(() => null),
    ])
      .then(([o, s, c]) => {
        setData(o);
        setSys(s);
        setCard(c);
      })
      .catch((e) => setErr(e.message));
  }, []);

  if (err) {
    return (
      <Alert>
        {err}
        {err.includes("Administrator") && (
          <> — you are signed in as a normal user. Set <span className="mono">
            ADMIN_EMAILS</span> on the backend, or sign in as the first
            registered account.</>
        )}
      </Alert>
    );
  }
  if (!data) return <Loading what="Loading system analytics" />;

  const t = data.totals;
  const acc = data.accuracy;

  return (
    <div className="stack">
      <div className="between">
        <h1 style={{ margin: 0 }}>Admin</h1>
        <span className="muted">
          system-wide · last {data.window_days} days
        </span>
      </div>

      {/* ---------------------------------------------------- totals --- */}
      <div className="grid cols-4">
        <Stat label="Users" value={t.users} />
        <Stat label="Sessions" value={t.sessions} />
        <Stat label="Fused reps" value={t.reps} tone="fused" />
        <Stat label="Habit logs" value={t.habit_logs} sub={`${t.diet_plans} diet plans`} />
      </div>

      {/* -------------------------------------------------- accuracy --- */}
      <Card
        title="Measured counting accuracy — all users"
        hint="Mean absolute error against human-labelled ground truth, across every labelled session in the database. This is the system-level evidence for the fusion claim."
      >
        {!acc ? (
          <Empty>
            No labelled sessions yet. Accuracy appears once users record the
            true rep count on a finished session.
          </Empty>
        ) : (
          <>
            <div className="grid cols-4">
              <Stat label="Camera MAE" value={acc.camera.mae} tone="camera" sub="reps/session" />
              <Stat label="IMU MAE" value={acc.imu.mae} tone="imu" sub="reps/session" />
              <Stat label="Fused MAE" value={acc.fused.mae} tone="fused" sub="reps/session" />
              <Stat
                label="Labelled sessions"
                value={acc.labelled_sessions}
                sub={`${acc.ground_truth_reps} true reps`}
              />
            </div>
            <p className="hint" style={{ marginTop: ".8rem", marginBottom: 0 }}>
              Totals counted — camera {acc.camera.total}, IMU {acc.imu.total},
              fused {acc.fused.total}, against {acc.ground_truth_reps} actual.
            </p>
          </>
        )}
      </Card>

      {/* -------------------------------------------------- activity --- */}
      {data.daily.length > 0 && (
        <div className="grid cols-2">
          <Card title="Platform activity">
            <ResponsiveContainer width="100%" height={230}>
              <LineChart data={data.daily} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke="#2a313c" vertical={false} />
                <XAxis dataKey="date" tick={AXIS} tickFormatter={(d) => d.slice(5)} />
                <YAxis tick={AXIS} allowDecimals={false} />
                <Tooltip {...TOOLTIP} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line type="monotone" dataKey="sessions" stroke="#58a6ff"
                      strokeWidth={2} dot={{ r: 3 }} name="Sessions" />
                <Line type="monotone" dataKey="reps" stroke="#3fb950"
                      strokeWidth={2} dot={{ r: 3 }} name="Reps" />
              </LineChart>
            </ResponsiveContainer>
          </Card>

          <Card title="Volume by exercise">
            {data.by_exercise.length === 0 ? (
              <Empty>Nothing logged yet.</Empty>
            ) : (
              <ResponsiveContainer width="100%" height={230}>
                <BarChart data={data.by_exercise}
                          margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                  <CartesianGrid stroke="#2a313c" vertical={false} />
                  <XAxis dataKey="exercise" tick={AXIS}
                         tickFormatter={(v) => v.replace(/_/g, " ")} />
                  <YAxis tick={AXIS} allowDecimals={false} />
                  <Tooltip {...TOOLTIP} />
                  <Bar dataKey="reps" fill="#3fb950" radius={[4, 4, 0, 0]} name="Reps" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </Card>
        </div>
      )}

      {/* ---------------------------------------------- form faults --- */}
      <Card
        title="Most common form faults"
        hint="Aggregated across every camera-detected repetition. Useful for deciding which coaching cue to improve first."
      >
        {data.form_faults.length === 0 ? (
          <Empty>No form faults recorded yet.</Empty>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr><th>Fault</th><th className="num">Occurrences</th></tr>
              </thead>
              <tbody>
                {data.form_faults.map((f) => (
                  <tr key={f.flag}>
                    <td>{FAULT_LABELS[f.flag] || f.flag}</td>
                    <td className="num">{f.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* --------------------------------------------------- users ---- */}
      <Card title="Users" hint="Newest first, up to 50.">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>#</th><th>Email</th><th>Name</th><th>Goal</th>
                <th>Joined</th><th className="num">Sessions</th>
                <th className="num">Reps</th><th className="num">Form</th>
              </tr>
            </thead>
            <tbody>
              {data.users.map((u) => (
                <tr key={u.id}>
                  <td>{u.id}</td>
                  <td className="mono">{u.email}</td>
                  <td>{u.full_name || <span className="muted">—</span>}</td>
                  <td>{u.goal}</td>
                  <td className="muted">{u.joined}</td>
                  <td className="num">{u.sessions}</td>
                  <td className="num">{u.reps}</td>
                  <td className="num">
                    {u.avg_form ? Math.round(u.avg_form * 100) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* -------------------------------------------------- system ---- */}
      {sys && (
        <Card title="Service health">
          <div className="grid cols-2">
            <table>
              <tbody>
                <tr>
                  <td>Environment</td>
                  <td className="right">{sys.environment}</td>
                </tr>
                <tr>
                  <td>Database</td>
                  <td className="right">
                    <Pill tone={sys.database.ok ? "ok" : "bad"}>
                      {sys.database.dialect} {sys.database.ok ? "ok" : "down"}
                    </Pill>
                  </td>
                </tr>
                <tr>
                  <td>MongoDB</td>
                  <td className="right mono">{sys.mongo}</td>
                </tr>
                <tr>
                  <td>MQTT</td>
                  <td className="right mono">{sys.mqtt}</td>
                </tr>
                <tr>
                  <td>Coach</td>
                  <td className="right mono">{sys.llm}</td>
                </tr>
              </tbody>
            </table>
            <table>
              <tbody>
                <tr>
                  <td>IMU samples seen</td>
                  <td className="num">{sys.imu_bus.samples_seen}</td>
                </tr>
                <tr>
                  <td>IMU reps detected</td>
                  <td className="num">{sys.imu_bus.reps_detected}</td>
                </tr>
                <tr>
                  <td>Match window</td>
                  <td className="num">{sys.fusion_defaults.match_window_s}s</td>
                </tr>
                <tr>
                  <td>Max clock offset</td>
                  <td className="num">{sys.fusion_defaults.max_offset_s}s</td>
                </tr>
                <tr>
                  <td>Min camera visibility</td>
                  <td className="num">{sys.fusion_defaults.min_camera_visibility}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* ----------------------------------------------- model card --- */}
      {card && (
        <Card title="Skip-risk model card">
          <div className="grid cols-4">
            <Stat label="ROC AUC" value={card.roc_auc} />
            <Stat label="Accuracy" value={card.accuracy} />
            <Stat label="Brier" value={card.brier} sub="lower is better" />
            <Stat label="Model" value={card.model_kind} sub={`n=${card.n_test}`} />
          </div>
          <Alert kind="warn">{card.warning}</Alert>
        </Card>
      )}
    </div>
  );
}
