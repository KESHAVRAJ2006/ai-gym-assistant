import React, { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { useAuth } from "../App.jsx";
import { Alert, Card, Field, Loading, Stat } from "../components/Ui.jsx";

function MacroBar({ label, achieved, target, colour }) {
  const pct = target > 0 ? Math.min(160, (achieved / target) * 100) : 0;
  const off = target > 0 ? Math.abs(achieved - target) / target : 0;
  return (
    <div style={{ marginBottom: ".7rem" }}>
      <div className="between" style={{ fontSize: ".82rem", marginBottom: ".2rem" }}>
        <span>{label}</span>
        <span className="mono">
          {achieved?.toFixed(0)} / {target?.toFixed(0)} g
          <span className="muted"> ({off > 0 ? `${(off * 100).toFixed(0)}% off` : "exact"})</span>
        </span>
      </div>
      <div className="gauge">
        <div style={{ width: `${Math.min(100, pct)}%`, background: colour }} />
      </div>
    </div>
  );
}

export default function Diet() {
  const { user } = useAuth();
  const [plan, setPlan] = useState(null);
  const [dietType, setDietType] = useState("veg");
  const [meals, setMeals] = useState(4);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    api
      .latestDiet()
      .then((p) => {
        if (p) {
          setPlan(p);
          setDietType(p.diet_type);
        }
      })
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  async function generate() {
    setBusy(true);
    setErr("");
    try {
      setPlan(await api.generateDiet({ diet_type: dietType, meals_per_day: meals }));
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (!loaded) return <Loading what="Loading your plan" />;

  const t = plan?.targets;
  const a = plan?.achieved;

  return (
    <div className="stack">
      <h1 style={{ margin: 0 }}>Diet</h1>
      <Alert>{err}</Alert>

      <Card
        title="Generate a plan"
        hint={`Uses your profile: ${user.weight_kg} kg, ${user.height_cm} cm, ${user.age} y, ${user.activity_level.replace("_", " ")}, goal "${user.goal}". Change those on the Profile tab.`}
      >
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div style={{ minWidth: "11rem" }}>
            <Field label="Diet type">
              <select value={dietType} onChange={(e) => setDietType(e.target.value)}>
                <option value="veg">Vegetarian</option>
                <option value="nonveg">Non-vegetarian</option>
                <option value="vegan">Vegan</option>
              </select>
            </Field>
          </div>
          <div style={{ minWidth: "9rem" }}>
            <Field label="Meals per day">
              <select value={meals} onChange={(e) => setMeals(Number(e.target.value))}>
                {[3, 4, 5, 6].map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </Field>
          </div>
          <button className="btn primary" onClick={generate} disabled={busy}
                  style={{ marginBottom: ".75rem" }}>
            {busy && <span className="spinner" />} Generate
          </button>
        </div>
      </Card>

      {!plan && (
        <Card>
          <p className="muted" style={{ margin: 0 }}>
            No plan yet. Press Generate.
          </p>
        </Card>
      )}

      {plan && (
        <>
          <div className="grid cols-4">
            <Stat label="BMR" value={Math.round(t.bmr)} sub="kcal/day at rest" />
            <Stat label="TDEE" value={Math.round(t.tdee)} sub="with activity" />
            <Stat label="Target" value={Math.round(t.target_kcal)} sub={`for "${user.goal}"`} tone="fused" />
            <Stat label="Plan total" value={Math.round(a.target_kcal)} sub="what the meals add up to" />
          </div>

          <Card
            title="Macro targets vs what the plan delivers"
            hint="The solver adjusts three portion sizes per meal until all three macros land close. Perfect agreement is not the goal - food comes in 5 g steps and realistic portions."
          >
            <MacroBar label="Protein" achieved={a.protein_g} target={t.protein_g} colour="#3fb950" />
            <MacroBar label="Carbohydrate" achieved={a.carbs_g} target={t.carbs_g} colour="#3b9ae1" />
            <MacroBar label="Fat" achieved={a.fat_g} target={t.fat_g} colour="#f0883e" />
            <p className="hint" style={{ marginTop: ".8rem", marginBottom: 0 }}>
              BMR from the Mifflin-St Jeor equation (1990); protein from Morton et al.
              (2018); food composition per 100 g from ICMR-NIN tables.
            </p>
          </Card>

          <div className="grid cols-2">
            {plan.meals.map((m) => (
              <Card key={m.slot} title={m.slot}
                    right={<span className="mono muted">{Math.round(m.kcal)} kcal</span>}>
                <table>
                  <thead>
                    <tr>
                      <th>Food</th><th className="num">g</th><th className="num">P</th>
                      <th className="num">C</th><th className="num">F</th>
                    </tr>
                  </thead>
                  <tbody>
                    {m.items.map((it) => (
                      <tr key={it.name}>
                        <td>{it.name}</td>
                        <td className="num">{it.grams}</td>
                        <td className="num">{it.protein_g.toFixed(0)}</td>
                        <td className="num">{it.carbs_g.toFixed(0)}</td>
                        <td className="num">{it.fat_g.toFixed(0)}</td>
                      </tr>
                    ))}
                    <tr>
                      <td className="muted">Total</td>
                      <td />
                      <td className="num">{m.protein_g.toFixed(0)}</td>
                      <td className="num">{m.carbs_g.toFixed(0)}</td>
                      <td className="num">{m.fat_g.toFixed(0)}</td>
                    </tr>
                  </tbody>
                </table>
              </Card>
            ))}
          </div>

          <p className="hint">
            This is a training-nutrition estimate for a healthy adult, not medical
            advice. If you have a medical condition, are pregnant, or are under 18,
            speak to a dietitian instead.
          </p>
        </>
      )}
    </div>
  );
}
