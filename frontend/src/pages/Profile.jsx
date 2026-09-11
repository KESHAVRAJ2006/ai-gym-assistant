import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { useAuth } from "../App.jsx";
import { Alert, Card, Field, Pill, Stat } from "../components/Ui.jsx";

export default function Profile() {
  const { user, setUser } = useAuth();
  const [form, setForm] = useState(user);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [imu, setImu] = useState(null);

  // Poll the IMU bus so you can watch the ESP32 come online while wiring it.
  useEffect(() => {
    const tick = () => api.imuStatus().then(setImu).catch(() => {});
    tick();
    const id = setInterval(tick, 5000);
    return () => clearInterval(id);
  }, []);

  const set = (k) => (e) => {
    const v = e.target.type === "number" ? Number(e.target.value) : e.target.value;
    setForm((f) => ({ ...f, [k]: v }));
    setOk(false);
  };

  async function save() {
    setBusy(true);
    setErr("");
    try {
      const updated = await api.updateMe({
        full_name: form.full_name,
        age: form.age,
        sex: form.sex,
        height_cm: form.height_cm,
        weight_kg: form.weight_kg,
        activity_level: form.activity_level,
        goal: form.goal,
        device_id: form.device_id,
      });
      setUser(updated);
      setForm(updated);
      setOk(true);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h1 style={{ margin: 0 }}>Profile</h1>
      <Alert>{err}</Alert>

      <Card title="Your details" hint="These feed the BMR equation, so keep them current.">
        <div className="grid cols-2">
          <Field label="Full name">
            <input value={form.full_name || ""} onChange={set("full_name")} />
          </Field>
          <Field label="Email">
            <input value={form.email} disabled />
          </Field>
          <Field label="Age">
            <input type="number" min="10" max="100" value={form.age} onChange={set("age")} />
          </Field>
          <Field label="Sex">
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
              <option value="sedentary">Sedentary</option>
              <option value="light">Light</option>
              <option value="moderate">Moderate</option>
              <option value="active">Active</option>
              <option value="very_active">Very active</option>
            </select>
          </Field>
          <Field label="Goal">
            <select value={form.goal} onChange={set("goal")}>
              <option value="cut">Cut</option>
              <option value="maintain">Maintain</option>
              <option value="bulk">Bulk</option>
            </select>
          </Field>
        </div>
        <div className="row">
          <button className="btn primary" onClick={save} disabled={busy}>
            {busy && <span className="spinner" />} Save
          </button>
          {ok && <Pill tone="ok">saved</Pill>}
        </div>
      </Card>

      <Card
        title="IMU device"
        hint="The device_id your ESP32 publishes under. MQTT topic: gym/<device_id>/imu. Leave blank to use the built-in simulator."
      >
        <Field label="Device ID">
          <input
            value={form.device_id || ""}
            onChange={set("device_id")}
            placeholder={`sim-${user.id}`}
          />
        </Field>
        {imu && (
          <>
            <div className="grid cols-3" style={{ marginBottom: ".6rem" }}>
              <Stat label="Samples seen" value={imu.bus.samples_seen} />
              <Stat label="IMU reps detected" value={imu.bus.reps_detected} tone="imu" />
              <Stat
                label="Last sample"
                value={
                  imu.bus.seconds_since_last_sample === null
                    ? "never"
                    : `${imu.bus.seconds_since_last_sample}s`
                }
                sub="ago"
              />
            </div>
            <p className="hint" style={{ margin: 0 }}>
              MQTT: <span className="mono">{imu.mqtt.status}</span>
              {imu.bus.devices_seen.length > 0 && (
                <>
                  {" "}| devices seen:{" "}
                  <span className="mono">{imu.bus.devices_seen.join(", ")}</span>
                </>
              )}
            </p>
          </>
        )}
      </Card>
    </div>
  );
}
