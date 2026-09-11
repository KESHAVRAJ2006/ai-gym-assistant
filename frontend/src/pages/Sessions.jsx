import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../lib/api.js";
import { Alert, Card, Empty, Loading, Pill } from "../components/Ui.jsx";

export default function Sessions() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState("");

  const load = () =>
    api.sessions(100).then(setRows).catch((e) => setErr(e.message));

  useEffect(() => {
    load();
  }, []);

  async function remove(id) {
    if (!window.confirm(`Delete session #${id}? This cannot be undone.`)) return;
    try {
      await api.deleteSession(id);
      load();
    } catch (e) {
      setErr(e.message);
    }
  }

  if (err) return <Alert>{err}</Alert>;
  if (!rows) return <Loading what="Loading sessions" />;

  const labelled = rows.filter((r) => r.ground_truth_reps !== null).length;

  return (
    <div className="stack">
      <h1 style={{ margin: 0 }}>Sessions</h1>

      {rows.length > 0 && labelled < rows.length && (
        <Alert kind="warn">
          {rows.length - labelled} of {rows.length} sessions have no ground-truth
          label. Unlabelled sessions cannot be used in your evaluation - open each
          one and record the rep count you actually did.
        </Alert>
      )}

      <Card className="pad-0">
        {rows.length === 0 ? (
          <div style={{ padding: "1rem" }}>
            <Empty>
              No sessions yet. Record one in the <Link to="/workout">Workout</Link> tab.
            </Empty>
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Date</th>
                  <th>Exercise</th>
                  <th className="num">Camera</th>
                  <th className="num">IMU</th>
                  <th className="num">Fused</th>
                  <th className="num">Truth</th>
                  <th className="num">Form</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => {
                  const err2 =
                    s.ground_truth_reps === null
                      ? null
                      : s.fused_reps - s.ground_truth_reps;
                  return (
                    <tr key={s.id}>
                      <td>
                        <Link to={`/sessions/${s.id}`}>{s.id}</Link>
                      </td>
                      <td className="muted">
                        {new Date(s.started_at).toLocaleString(undefined, {
                          month: "short", day: "numeric",
                          hour: "2-digit", minute: "2-digit",
                        })}
                      </td>
                      <td>{s.exercise.replace(/_/g, " ")}</td>
                      <td className="num" style={{ color: "var(--camera)" }}>{s.camera_reps}</td>
                      <td className="num" style={{ color: "var(--imu)" }}>{s.imu_reps}</td>
                      <td className="num" style={{ color: "var(--fused)" }}>{s.fused_reps}</td>
                      <td className="num">
                        {s.ground_truth_reps === null ? (
                          <Pill tone="warn">none</Pill>
                        ) : (
                          <span>
                            {s.ground_truth_reps}
                            {err2 !== 0 && (
                              <span className="muted"> ({err2 > 0 ? "+" : ""}{err2})</span>
                            )}
                          </span>
                        )}
                      </td>
                      <td className="num">{Math.round(s.avg_form_score * 100)}</td>
                      <td className="right">
                        <button className="btn sm danger" onClick={() => remove(s.id)}>
                          Delete
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
