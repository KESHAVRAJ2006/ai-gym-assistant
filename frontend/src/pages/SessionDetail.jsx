/**
 * One session in detail, plus the interactive fusion re-run.
 *
 * The threshold sliders re-run `fusion_engine.fuse` on the stored session
 * server-side. That is the sensitivity analysis from the report, done on real
 * recorded data, without re-recording anything - and it is a very good thing
 * to demonstrate live in a viva.
 */
import React, { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
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

export default function SessionDetail() {
  const { id } = useParams();
  const [s, setS] = useState(null);
  const [trace, setTrace] = useState(null);
  const [err, setErr] = useState("");
  const [gt, setGt] = useState("");
  const [win, setWin] = useState(0.6);
  const [vis, setVis] = useState(0.55);
  const [refused, setRefused] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .session(id)
      .then((d) => {
        setS(d);
        setGt(d.ground_truth_reps ?? "");
      })
      .catch((e) => setErr(e.message));
    api.sessionTrace(id).then(setTrace).catch(() => {});
  }, [id]);

  const rerun = useCallback(async () => {
    setBusy(true);
    try {
      setRefused(await api.refuse(id, win, vis));
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }, [id, win, vis]);

  async function saveGt() {
    try {
      const updated = await api.patchSession(id, { ground_truth_reps: Number(gt) });
      setS((prev) => ({ ...prev, ground_truth_reps: updated.ground_truth_reps }));
    } catch (e) {
      setErr(e.message);
    }
  }

  if (err) return <Alert>{err}</Alert>;
  if (!s) return <Loading what="Loading session" />;

  const byType = (t) => s.reps.filter((r) => r.source === t);
  const chart = (trace?.frames || []).map((f) => ({
    t: Number(f.t?.toFixed?.(1) ?? f.t),
    angle: f.angle,
    vis: (f.vis ?? 1) * 180,
  }));

  return (
    <div className="stack">
      <div className="between">
        <h1 style={{ margin: 0 }}>
          Session #{s.id} &mdash; {s.exercise.replace(/_/g, " ")}
        </h1>
        <span className="muted">{new Date(s.started_at).toLocaleString()}</span>
      </div>

      <div className="grid cols-4">
        <Stat label="Camera" value={s.camera_reps} tone="camera" />
        <Stat label="IMU" value={s.imu_reps} tone="imu" />
        <Stat label="Fused" value={s.fused_reps} tone="fused" />
        <Stat
          label="Ground truth"
          value={s.ground_truth_reps ?? "-"}
          sub={s.ground_truth_reps === null ? "not labelled" : "your count"}
        />
      </div>

      <Card
        title="Ground truth label"
        hint="The one number that makes this session usable as evidence."
      >
        <div className="row">
          <input
            type="number"
            min="0"
            value={gt}
            onChange={(e) => setGt(e.target.value)}
            style={{ width: "9rem" }}
          />
          <button className="btn primary" onClick={saveGt} disabled={gt === ""}>
            Save
          </button>
          {s.ground_truth_reps !== null && (
            <span className="muted">
              Fused error: {s.fused_reps - s.ground_truth_reps} rep(s) | camera error:{" "}
              {s.camera_reps - s.ground_truth_reps} | IMU error:{" "}
              {s.imu_reps - s.ground_truth_reps}
            </span>
          )}
        </div>
      </Card>

      {chart.length > 0 && (
        <Card
          title="Joint angle trace"
          hint="Reconstructed from the landmark archive in MongoDB. Each trough is one repetition."
        >
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={chart} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="#2a313c" vertical={false} />
              <XAxis dataKey="t" tick={AXIS} unit="s" />
              <YAxis tick={AXIS} domain={[0, 190]} />
              <Tooltip {...TOOLTIP} />
              <Line type="monotone" dataKey="angle" stroke="#3b9ae1" strokeWidth={2}
                    dot={false} name="Joint angle" isAnimationActive={false} />
              <Line type="monotone" dataKey="vis" stroke="#6b7784" strokeWidth={1}
                    dot={false} name="Visibility (scaled)" isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}
      {trace && chart.length === 0 && (
        <Card title="Joint angle trace">
          <Empty>
            No archived frames for this session. The angle trace needs MongoDB, which
            reports: <span className="mono">{trace.mongo}</span>
          </Empty>
        </Card>
      )}

      <Card
        title="Re-run fusion with different thresholds"
        hint="Recomputes the fused count on this stored session. This is the parameter sensitivity analysis, on real data, live."
      >
        <div className="grid cols-2">
          <label className="field">
            <span>Match window: {win.toFixed(2)} s</span>
            <input type="range" min="0.1" max="1.5" step="0.05" value={win}
                   onChange={(e) => setWin(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Low-visibility threshold: {vis.toFixed(2)}</span>
            <input type="range" min="0" max="1" step="0.05" value={vis}
                   onChange={(e) => setVis(Number(e.target.value))} />
          </label>
        </div>
        <button className="btn accent" onClick={rerun} disabled={busy}>
          {busy && <span className="spinner" />} Re-run fusion
        </button>

        {refused && (
          <div style={{ marginTop: "1rem" }}>
            <div className="grid cols-4">
              <Stat label="Matched" value={refused.matched} />
              <Stat label="Camera only" value={refused.camera_only} tone="camera" />
              <Stat label="IMU only" value={refused.imu_only} tone="imu" />
              <Stat label="Fused total" value={refused.fused_count} tone="fused" />
            </div>
            <p className="hint" style={{ marginTop: ".6rem" }}>
              Estimated clock offset {refused.offset_s}s | mean confidence{" "}
              {refused.mean_confidence}
              {refused.vs_ground_truth && (
                <>
                  {" "}| errors vs truth: camera {refused.vs_ground_truth.camera_abs_error},
                  IMU {refused.vs_ground_truth.imu_abs_error}, fused{" "}
                  {refused.vs_ground_truth.fused_abs_error}
                </>
              )}
            </p>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th className="num">t</th><th>Origin</th><th>Decision</th>
                    <th className="num">Conf</th><th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {refused.reps.map((r, i) => (
                    <tr key={i}>
                      <td className="num">{r.t?.toFixed(2)}</td>
                      <td>
                        <Pill tone={r.origin === "both" ? "ok" : r.origin === "camera_only" ? "camera" : "imu"}>
                          {r.origin}
                        </Pill>
                      </td>
                      <td>
                        <Pill tone={r.accepted ? "ok" : "bad"}>
                          {r.accepted ? "accepted" : "rejected"}
                        </Pill>
                      </td>
                      <td className="num">{r.confidence?.toFixed(2)}</td>
                      <td className="muted">{r.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Card>

      <Card title="Repetitions as stored">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Source</th><th className="num">#</th><th className="num">ROM</th>
                <th className="num">Tempo</th><th className="num">Form</th><th>Flags</th>
              </tr>
            </thead>
            <tbody>
              {["camera", "imu", "fused"].flatMap((src) =>
                byType(src).map((r, i) => (
                  <tr key={`${src}-${i}`}>
                    <td><Pill tone={src === "fused" ? "ok" : src}>{src}</Pill></td>
                    <td className="num">{r.rep_index}</td>
                    <td className="num">{r.rom_deg ? `${r.rom_deg.toFixed(0)}°` : "-"}</td>
                    <td className="num">{r.tempo_s ? `${r.tempo_s.toFixed(1)}s` : "-"}</td>
                    <td className="num">{Math.round(r.form_score * 100)}</td>
                    <td>
                      <span className="flags">
                        {(r.flags || []).map((f) => (
                          <Pill key={f} tone="warn">{f}</Pill>
                        ))}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
