/** Small presentational pieces shared by every page. */
import React from "react";

export function Card({ title, hint, right, children, className = "" }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <div className="between" style={{ marginBottom: hint ? ".15rem" : ".6rem" }}>
          {title && <h3 style={{ margin: 0 }}>{title}</h3>}
          {right}
        </div>
      )}
      {hint && <p className="hint" style={{ marginBottom: ".75rem" }}>{hint}</p>}
      {children}
    </section>
  );
}

export function Stat({ label, value, sub, tone = "" }) {
  return (
    <div className="card stat">
      <span className="label">{label}</span>
      <span className={`value ${tone}`}>{value}</span>
      {sub && <span className="sub">{sub}</span>}
    </div>
  );
}

export function Alert({ kind = "error", children }) {
  if (!children) return null;
  const cls = kind === "error" ? "alert" : `alert ${kind}`;
  return <div className={cls} role={kind === "error" ? "alert" : "status"}>{children}</div>;
}

export function Field({ label, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

export function Pill({ tone = "", children }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function Loading({ what = "Loading" }) {
  return (
    <div className="row muted" style={{ padding: "1rem 0" }}>
      <span className="spinner" /> {what}...
    </div>
  );
}

export function Empty({ children }) {
  return <p className="muted" style={{ padding: ".5rem 0", margin: 0 }}>{children}</p>;
}
