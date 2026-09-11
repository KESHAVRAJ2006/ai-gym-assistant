import React, { useState } from "react";
import { useAuth } from "../App.jsx";
import { api } from "../lib/api.js";
import { Alert, Field } from "../components/Ui.jsx";

const BLANK = {
  email: "",
  password: "",
  full_name: "",
  age: 21,
  sex: "male",
  height_cm: 170,
  weight_kg: 65,
  activity_level: "moderate",
  goal: "maintain",
};

export default function Auth() {
  const { login } = useAuth();
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState(BLANK);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const set = (k) => (e) => {
    const v = e.target.type === "number" ? Number(e.target.value) : e.target.value;
    setForm((f) => ({ ...f, [k]: v }));
  };

  async function submit(e) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const res =
        mode === "login"
          ? await api.login(form.email, form.password)
          : await api.register(form);
      login(res.access_token, res.user);
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card card">
        <h1 style={{ marginBottom: ".25rem" }}>AI Gym &amp; Fitness Assistant</h1>
        <p className="hint" style={{ marginBottom: "1rem" }}>
          Camera + IMU sensor fusion for rep counting and form validation.
        </p>

        <div className="tabs">
          <button
            type="button"
            className={`btn ${mode === "login" ? "accent" : ""}`}
            onClick={() => { setMode("login"); setErr(""); }}
          >
            Sign in
          </button>
          <button
            type="button"
            className={`btn ${mode === "register" ? "accent" : ""}`}
            onClick={() => { setMode("register"); setErr(""); }}
          >
            Create account
          </button>
        </div>

        <Alert>{err}</Alert>

        <form onSubmit={submit}>
          <Field label="Email">
            <input type="email" required value={form.email} onChange={set("email")}
                   autoComplete="email" placeholder="you@college.edu" />
          </Field>
          <Field label="Password">
            <input type="password" required minLength={6} value={form.password}
                   onChange={set("password")}
                   autoComplete={mode === "login" ? "current-password" : "new-password"}
                   placeholder="at least 6 characters" />
          </Field>

          {mode === "register" && (
            <>
              <Field label="Full name">
                <input value={form.full_name} onChange={set("full_name")} placeholder="Your name" />
              </Field>
              <div className="grid cols-2">
                <Field label="Age">
                  <input type="number" min="10" max="100" value={form.age} onChange={set("age")} />
                </Field>
                <Field label="Sex (for the BMR equation)">
                  <select value={form.sex} onChange={set("sex")}>
                    <option value="male">Male</option>
                    <option value="female">Female</option>
                  </select>
                </Field>
                <Field label="Height (cm)">
                  <input type="number" min="100" max="250" value={form.height_cm}
                         onChange={set("height_cm")} />
                </Field>
                <Field label="Weight (kg)">
                  <input type="number" min="25" max="300" step="0.5" value={form.weight_kg}
                         onChange={set("weight_kg")} />
                </Field>
                <Field label="Activity level">
                  <select value={form.activity_level} onChange={set("activity_level")}>
                    <option value="sedentary">Sedentary - no training</option>
                    <option value="light">Light - 1 to 3 sessions/week</option>
                    <option value="moderate">Moderate - 3 to 5 sessions/week</option>
                    <option value="active">Active - 6 to 7 sessions/week</option>
                    <option value="very_active">Very active - twice daily</option>
                  </select>
                </Field>
                <Field label="Goal">
                  <select value={form.goal} onChange={set("goal")}>
                    <option value="cut">Cut - lose fat</option>
                    <option value="maintain">Maintain</option>
                    <option value="bulk">Bulk - gain muscle</option>
                  </select>
                </Field>
              </div>
            </>
          )}

          <button className="btn primary" style={{ width: "100%" }} disabled={busy}>
            {busy && <span className="spinner" />}
            {mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>

        <p className="hint" style={{ marginTop: ".9rem", marginBottom: 0 }}>
          The camera never leaves your device - only 33 skeleton coordinates per
          frame are sent to the server.
        </p>
      </div>
    </div>
  );
}
