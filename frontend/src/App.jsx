import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";

import { api, clearSession, getStoredUser, getToken, setSession } from "./lib/api.js";
import Admin from "./pages/Admin.jsx";
import Auth from "./pages/Auth.jsx";
import Coach from "./pages/Coach.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Diet from "./pages/Diet.jsx";
import Habits from "./pages/Habits.jsx";
import Profile from "./pages/Profile.jsx";
import SessionDetail from "./pages/SessionDetail.jsx";
import Sessions from "./pages/Sessions.jsx";
import Workout from "./pages/Workout.jsx";

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

const TABS = [
  ["/", "Dashboard"],
  ["/workout", "Workout"],
  ["/sessions", "Sessions"],
  ["/diet", "Diet"],
  ["/habits", "Habits"],
  ["/coach", "Coach"],
];

export default function App() {
  const [user, setUser] = useState(getStoredUser);
  const [booting, setBooting] = useState(Boolean(getToken()));
  const navigate = useNavigate();

  // A stored token may be expired or point at a database that has been
  // reset (which happens every time Render's free Postgres expires), so
  // always re-validate it against /api/auth/me on boot.
  useEffect(() => {
    if (!getToken()) {
      setBooting(false);
      return;
    }
    let cancelled = false;
    api
      .me()
      .then((u) => !cancelled && setUser(u))
      .catch(() => {
        if (!cancelled) {
          clearSession();
          setUser(null);
        }
      })
      .finally(() => !cancelled && setBooting(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback((token, u) => {
    setSession(token, u);
    setUser(u);
  }, []);

  const logout = useCallback(() => {
    clearSession();
    setUser(null);
    navigate("/");
  }, [navigate]);

  const ctx = useMemo(() => ({ user, setUser, login, logout }), [user, login, logout]);

  if (booting) {
    return (
      <div className="auth-wrap">
        <div className="row muted">
          <span className="spinner" /> Waking the server up...
        </div>
      </div>
    );
  }

  if (!user) {
    return (
      <AuthContext.Provider value={ctx}>
        <Auth />
      </AuthContext.Provider>
    );
  }

  return (
    <AuthContext.Provider value={ctx}>
      <div className="app">
        <nav className="nav">
          <span className="brand">
            AI Gym<span>.</span>
          </span>
          {TABS.map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) => `tab${isActive ? " active" : ""}`}
            >
              {label}
            </NavLink>
          ))}
          <span className="spacer" />
          {user.is_admin && (
            <NavLink
              to="/admin"
              className={({ isActive }) => `tab${isActive ? " active" : ""}`}
              style={{ color: "var(--warn)" }}
            >
              Admin
            </NavLink>
          )}
          <NavLink to="/profile" className={({ isActive }) => `tab${isActive ? " active" : ""}`}>
            {user.full_name || user.email.split("@")[0]}
          </NavLink>
          <button className="btn sm" onClick={logout}>
            Sign out
          </button>
        </nav>

        <main className="main">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/workout" element={<Workout />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/sessions/:id" element={<SessionDetail />} />
            <Route path="/diet" element={<Diet />} />
            <Route path="/habits" element={<Habits />} />
            <Route path="/coach" element={<Coach />} />
            <Route path="/profile" element={<Profile />} />
            <Route path="/admin" element={<Admin />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </AuthContext.Provider>
  );
}
