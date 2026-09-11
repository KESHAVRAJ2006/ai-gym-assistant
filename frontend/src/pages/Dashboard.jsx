import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

import { api } from "../lib/api.js";
import { useAuth } from "../App.jsx";
import { Alert, Card, Empty, Loading, Pill, Stat } from "../components/Ui.jsx";

const AXIS = { stroke: "#6b7784", fontSize: 11 };
const TOOLTIP = {
  contentStyle: {
    background: "#161b22", border: "1px solid #2a313c",
    borderRadius: 8, fontSize: 12, color: "#e6edf3",
  },
};

export default function Dashboard() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [streak, setStreak] = useState(null);
  const [health, setHealth] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([
      api.sessionSummary(30),
      api.streak().catch(() => null),
      api.health().catch(() => null),
    ])
      .then(([s, st, h]) => {
        setData(s);
        setStreak(st);
        setHealth(h);
      })
      .catch((e) => setErr(e.message));
  }, []);

  if (err) return <Alert>{err}</Alert>;
  if (!data) return <Loading what="Loading your dashboard" />;

  const hasSessions = data.total_sessions > 0;

  return (
    <div className="stack">
      <div className="between">
        <h1 style={{ margin: 0 }}>
          Hello{user.full_name ? `, ${user.full_name.split(" ")[0]}` : ""}
        </h1>
        <div className="row">
          {health && (
            <>
              <Pill tone={health.status === "ok" ? "ok" : "warn"}>
                api {health.status}
              </Pill>
              <Pill tone={health.mongo?.startsWith("connected") ? "ok" : ""}>
                mongo {health.mongo?.split(" ")[0]}
              </Pill>
              <Pill tone={health.mqtt?.startsWith("connected") ? "ok" : ""}>
                mqtt {health.mqtt?.split(" ")[0]}
              </Pill>
            </>
          )}
        </div>
      </div>

      <div className="grid cols-4">
        <Stat label="Sessions (30d)" value={data.total_sessions} />
        <Stat label="Fused reps (30d)" value={data.total_reps} tone="fused" />
        <Stat
          label="Mean form score"
          value={hasSessions ? Math.round(data.mean_form_score * 100) : "-"}
          sub="out of 100"
        />
        <Stat
          label="Current streak"
          value={streak?.current_streak ?? 0}
          sub={`best ${streak?.best_streak ?? 0} days`}
        />
      </div>

      {!hasSessions && (
        <Card title="No workouts yet">
          <p>
            Head to the <Link to="/workout">Workout</Link> tab, allow the camera and do
            a set of bicep curls. Everything on this page fills in from real measured
            sessions - nothing here is sample data.
          </p>
        </Card>
      )}

      {data.accuracy && (
        <Card
          title="Measured counting accuracy"
          hint={`Mean absolute error against your own labels, over ${data.accuracy.n_labelled_sessions} labelled session(s). This is the table your report needs - label every session you record.`}
        >
          <div className="grid cols-3">
            <Stat label="Camera MAE" value={data.accuracy.camera_mae} tone="camera" sub="reps/session" />
            <Stat label="IMU MAE" value={data.accuracy.imu_mae} tone="imu" sub="reps/session" />
            <Stat label="Fused MAE" value={data.accuracy.fused_mae} tone="fused" sub="reps/session" />
          </div>
        </Card>
      )}

      {hasSessions && (
        <div className="grid cols-2">
          <Card title="Reps per day">
            <ResponsiveContainer width="100%" height={230}>
              <BarChart data={data.daily} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke="#2a313c" vertical={false} />
                <XAxis dataKey="date" tick={AXIS} tickFormatter={(d) => d.slice(5)} />
                <YAxis tick={AXIS} allowDecimals={false} />
                <Tooltip {...TOOLTIP} />
                <Bar dataKey="reps" fill="#3fb950" radius={[4, 4, 0, 0]} name="Fused reps" />
              </BarChart>
            </ResponsiveContainer>
          </Card>

          <Card title="Form score trend">
            <ResponsiveContainer width="100%" height={230}>
              <LineChart data={data.daily} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke="#2a313c" vertical={false} />
                <XAxis dataKey="date" tick={AXIS} tickFormatter={(d) => d.slice(5)} />
                <YAxis tick={AXIS} domain={[0, 1]} />
                <Tooltip {...TOOLTIP} />
                <Line type="monotone" dataKey="avg_form" stroke="#58a6ff" strokeWidth={2}
                      dot={{ r: 3 }} name="Mean form" />
              </LineChart>
            </ResponsiveContainer>
          </Card>
        </div>
      )}

      {data.agreement?.length > 0 && (
        <Card
          title="Sensor agreement per session"
          hint="Where the three bars differ, fusion had to make a decision. Those sessions are the interesting ones for your report."
        >
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={data.agreement} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="#2a313c" vertical={false} />
              <XAxis dataKey="session_id" tick={AXIS}
                     tickFormatter={(v) => `#${v}`} />
              <YAxis tick={AXIS} allowDecimals={false} />
              <Tooltip {...TOOLTIP} labelFormatter={(v) => `Session #${v}`} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="camera" fill="#3b9ae1" name="Camera" radius={[3, 3, 0, 0]} />
              <Bar dataKey="imu" fill="#f0883e" name="IMU" radius={[3, 3, 0, 0]} />
              <Bar dataKey="fused" fill="#3fb950" name="Fused" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      )}

      {data.by_exercise?.length > 0 && (
        <Card title="Volume by exercise">
          <div className="table-scroll">
            <table>
              <thead>
                <tr><th>Exercise</th><th className="num">Fused reps</th></tr>
              </thead>
              <tbody>
                {data.by_exercise.map((r) => (
                  <tr key={r.exercise}>
                    <td>{r.exercise.replace(/_/g, " ")}</td>
                    <td className="num">{r.reps}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.by_exercise.length === 0 && <Empty>Nothing logged yet.</Empty>}
        </Card>
      )}
    </div>
  );
}
