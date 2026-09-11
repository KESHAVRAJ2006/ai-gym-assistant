import React, { useCallback, useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

import { api } from "../lib/api.js";
import { Alert, Card, Field, Loading, Pill, Stat } from "../components/Ui.jsx";

const AXIS = { stroke: "#6b7784", fontSize: 11 };
const TOOLTIP = {
  contentStyle: {
    background: "#161b22", border: "1px solid #2a313c",
    borderRadius: 8, fontSize: 12, color: "#e6edf3",
  },
};
const today = () => new Date().toISOString().slice(0, 10);

export default function Habits() {
  const [streak, setStreak] = useState(null);
  const [risk, setRisk] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const [form, setForm] = useState({
    log_date: today(), completed: true, duration_min: 45,
    sleep_h: 7, soreness: 2, mood: 3, planned_hour: 18,
  });

  const load = useCallback(() => {
    api.streak().then(setStreak).catch((e) => setErr(e.message));
    api
      .skipRisk({
        planned_hour: form.planned_hour, sleep_h: form.sleep_h,
        soreness: form.soreness, mood: form.mood,
      })
      .then(setRisk)
      .catch(() => {});
  }, [form.planned_hour, form.sleep_h, form.soreness, form.mood]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    api.habitModelMetrics().then(setMetrics).catch(() => {});
  }, []);

  const set = (k) => (e) => {
    const v =
      e.target.type === "checkbox"
        ? e.target.checked
        : e.target.type === "number" || e.target.type === "range"
          ? Number(e.target.value)
          : e.target.value;
    setForm((f) => ({ ...f, [k]: v }));
  };

  async function save() {
    setBusy(true);
    setErr("");
    try {
      await api.logHabit({ ...form, planned: true });
      load();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function seed() {
    setBusy(true);
    try {
      await api.seedHabits(45);
      load();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (!streak) return <Loading what="Loading habits" />;

  const cal = (streak.calendar || []).slice(-42).map((d) => ({
    date: d.date.slice(5),
    minutes: d.duration_min,
    done: d.completed ? 1 : 0,
  }));

  const tone = risk?.risk_band === "low" ? "ok" : risk?.risk_band === "high" ? "bad" : "warn";

  return (
    <div className="stack">
      <h1 style={{ margin: 0 }}>Habits</h1>
      <Alert>{err}</Alert>

      <div className="grid cols-4">
        <Stat label="Current streak" value={streak.current_streak} sub="days" />
        <Stat label="Best streak" value={streak.best_streak} sub="days" />
        <Stat label="Days logged" value={streak.days_logged} />
        <Stat
          label="30-day completion"
          value={`${Math.round((streak.completion_rate_30d || 0) * 100)}%`}
        />
      </div>

      {risk && (
        <Card
          title="Today's skip risk"
          right={<Pill tone={tone}>{risk.risk_band}</Pill>}
          hint={`Predicted by an ${risk.model_kind} model from your logged history plus the state you report below.`}
        >
          <div className="row" style={{ alignItems: "baseline", marginBottom: ".5rem" }}>
            <span className="value" style={{ fontSize: "2.4rem", fontWeight: 700 }}>
              {Math.round(risk.skip_probability * 100)}%
            </span>
            <span className="muted">chance you skip today</span>
          </div>
          <div className="gauge" style={{ marginBottom: ".8rem" }}>
            <div
              style={{
                width: `${risk.skip_probability * 100}%`,
                background:
                  tone === "ok" ? "var(--fused)" : tone === "bad" ? "var(--danger)" : "var(--warn)",
              }}
            />
          </div>
          <p style={{ marginBottom: ".4rem" }}>{risk.recommendation}</p>
          <p className="hint" style={{ margin: 0 }}>
            Main drivers: {risk.top_drivers.join("; ")}
          </p>
        </Card>
      )}

      <div className="grid cols-2">
        <Card title="Log today" hint="One row per day. Saving the same date again updates it.">
          <Field label="Date">
            <input type="date" value={form.log_date} onChange={set("log_date")} />
          </Field>
          <label className="field">
            <span>Completed the session?</span>
            <div className="row">
              <input type="checkbox" checked={form.completed} onChange={set("completed")}
                     style={{ width: "auto" }} />
              <span className="muted">{form.completed ? "Yes" : "No"}</span>
            </div>
          </label>
          <div className="grid cols-2">
            <Field label={`Duration: ${form.duration_min} min`}>
              <input type="range" min="0" max="120" step="5" value={form.duration_min}
                     onChange={set("duration_min")} />
            </Field>
            <Field label={`Sleep: ${form.sleep_h} h`}>
              <input type="range" min="0" max="12" step="0.5" value={form.sleep_h}
                     onChange={set("sleep_h")} />
            </Field>
            <Field label={`Soreness: ${form.soreness}/5`}>
              <input type="range" min="0" max="5" value={form.soreness} onChange={set("soreness")} />
            </Field>
            <Field label={`Mood: ${form.mood}/5`}>
              <input type="range" min="1" max="5" value={form.mood} onChange={set("mood")} />
            </Field>
          </div>
          <Field label={`Planned start hour: ${form.planned_hour}:00`}>
            <input type="range" min="0" max="23" value={form.planned_hour}
                   onChange={set("planned_hour")} />
          </Field>
          <div className="btn-row">
            <button className="btn primary" onClick={save} disabled={busy}>
              {busy && <span className="spinner" />} Save log
            </button>
            <button className="btn" onClick={seed} disabled={busy}>
              Fill demo history
            </button>
          </div>
        </Card>

        <Card title="Last 6 weeks" hint="Minutes trained per day.">
          {cal.length === 0 ? (
            <p className="muted">Nothing logged yet. Save a day, or fill demo history.</p>
          ) : (
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={cal} margin={{ top: 6, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid stroke="#2a313c" vertical={false} />
                <XAxis dataKey="date" tick={AXIS} interval={5} />
                <YAxis tick={AXIS} />
                <Tooltip {...TOOLTIP} />
                <Bar dataKey="minutes" fill="#3fb950" radius={[3, 3, 0, 0]} name="Minutes" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      {metrics && (
        <Card
          title="Skip-risk model card"
          hint="Report these numbers exactly as they are. They are measured on held-out SYNTHETIC users, so they validate the pipeline, not human behaviour. Replace them with your own logged data before submission."
        >
          <div className="grid cols-4">
            <Stat label="ROC AUC" value={metrics.roc_auc} />
            <Stat label="Accuracy" value={metrics.accuracy} />
            <Stat label="Brier score" value={metrics.brier} sub="lower is better" />
            <Stat label="Model" value={metrics.model_kind} sub={`n=${metrics.n_test} test`} />
          </div>
        </Card>
      )}
    </div>
  );
}
