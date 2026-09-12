/**
 * Tiny API client.
 *
 * No axios: fetch does everything we need, and one fewer dependency is one
 * fewer thing to explain. The JWT lives in localStorage - acceptable for a
 * single-page college project, and called out in the report's limitations
 * section because it is vulnerable to XSS in a way an httpOnly cookie is not.
 */

/**
 * Where the backend lives.
 *
 * 1. VITE_API_BASE, baked in at build time. This is the intended mechanism.
 * 2. If that is empty AND we are served from *.onrender.com, derive the API
 *    host from our own hostname (aigym-web -> aigym-api).
 * 3. Otherwise empty, which in dev makes calls relative so Vite can proxy.
 *
 * Step 2 exists because of a real failure: Render reported a deploy as Live
 * without rebuilding the bundle, so VITE_API_BASE never made it into the
 * JavaScript. Every API call then went to the static site itself, whose
 * SPA rewrite answers POSTs with an empty 200 - which looks like a totally
 * unrelated frontend crash. Deriving the host makes the app correct even
 * when the build-time variable goes missing.
 */
function resolveApiBase() {
  const fromEnv = (import.meta.env.VITE_API_BASE || "").trim();
  if (fromEnv) return fromEnv.replace(/\/$/, "");

  if (typeof window !== "undefined") {
    const { hostname, protocol } = window.location;
    if (hostname.endsWith(".onrender.com") && hostname.includes("-web")) {
      return `${protocol}//${hostname.replace("-web", "-api")}`;
    }
  }
  return "";
}

export const API_BASE = resolveApiBase();

const TOKEN_KEY = "aigym_token";
const USER_KEY = "aigym_user";

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getStoredUser() {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function setSession(token, user) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  } catch {
    /* private-browsing mode: the session simply will not persist */
  }
}

export function clearSession() {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(path, { method = "GET", body, auth = true } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) {
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    // A free Render service sleeps after 15 minutes and takes ~50 s to wake.
    // Say so, rather than showing a bare "Failed to fetch".
    throw new ApiError(
      "Cannot reach the server. If it is hosted on Render's free tier it may " +
        "be waking up - wait about 50 seconds and try again.",
      0
    );
  }

  if (res.status === 204) return null;

  const text = await res.text();

  // An OK response with no body means we are not talking to the API at all -
  // almost always because API_BASE is empty and the request hit the static
  // site, whose SPA rewrite answers with an empty 200. Returning null here
  // made callers blow up later with "Cannot read properties of null", which
  // points at the wrong file entirely. Fail loudly, and say why.
  if (res.ok && !text.trim()) {
    throw new ApiError(
      `The server returned an empty response for ${path}. The app is probably ` +
        `pointing at the wrong address (currently "${API_BASE || "same origin"}"). ` +
        `Rebuild the front-end with VITE_API_BASE set to the backend URL.`,
      res.status
    );
  }

  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // HTML instead of JSON is the same misconfiguration wearing a hat.
    if (res.ok && text.trimStart().startsWith("<")) {
      throw new ApiError(
        `Expected JSON from ${path} but received an HTML page. The app is ` +
          `pointing at the wrong address (currently "${API_BASE || "same origin"}").`,
        res.status
      );
    }
    data = { detail: text };
  }

  if (!res.ok) {
    if (res.status === 401) clearSession();
    const detail = data?.detail;
    const msg =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => `${d.loc?.slice(-1)[0] ?? ""}: ${d.msg}`).join("; ")
          : `Request failed (${res.status})`;
    throw new ApiError(msg, res.status);
  }
  return data;
}

export const api = {
  // ---- auth ----
  register: (payload) =>
    request("/api/auth/register", { method: "POST", body: payload, auth: false }),
  login: (email, password) =>
    request("/api/auth/login", { method: "POST", body: { email, password }, auth: false }),
  me: () => request("/api/auth/me"),
  updateMe: (payload) => request("/api/auth/me", { method: "PATCH", body: payload }),

  // ---- sessions ----
  exercises: () => request("/api/exercises", { auth: false }),
  sessions: (limit = 50) => request(`/api/sessions?limit=${limit}`),
  sessionSummary: (days = 30) => request(`/api/sessions/summary?days=${days}`),
  session: (id) => request(`/api/sessions/${id}`),
  sessionTrace: (id) => request(`/api/sessions/${id}/trace`),
  patchSession: (id, payload) =>
    request(`/api/sessions/${id}`, { method: "PATCH", body: payload }),
  refuse: (id, win, vis) =>
    request(`/api/sessions/${id}/refuse?match_window_s=${win}&low_vis_threshold=${vis}`, {
      method: "POST",
    }),
  deleteSession: (id) => request(`/api/sessions/${id}`, { method: "DELETE" }),

  // ---- diet ----
  generateDiet: (payload) =>
    request("/api/diet/generate", { method: "POST", body: payload }),
  latestDiet: () => request("/api/diet/latest"),

  // ---- habits ----
  habits: (days = 60) => request(`/api/habits?days=${days}`),
  logHabit: (payload) => request("/api/habits/log", { method: "POST", body: payload }),
  skipRisk: (q) => request(`/api/habits/skip-risk?${new URLSearchParams(q)}`),
  streak: () => request("/api/habits/streak"),
  habitModelMetrics: () => request("/api/habits/model-metrics", { auth: false }),
  seedHabits: (days = 45) =>
    request(`/api/habits/seed-demo?days=${days}`, { method: "POST" }),

  // ---- chat ----
  chat: (message) => request("/api/chat", { method: "POST", body: { message } }),

  // ---- imu ----
  imuStatus: () => request("/api/imu/status", { auth: false }),
  simulateImu: (q) =>
    request(`/api/imu/simulate?${new URLSearchParams(q)}`, { method: "POST" }),

  // ---- admin ----
  adminOverview: (days = 30) => request(`/api/admin/overview?days=${days}`),
  adminSystem: () => request("/api/admin/system"),
  adminModelCard: () => request("/api/admin/model-card"),

  // ---- meta ----
  health: () => request("/health", { auth: false }),
};

/** Build the WebSocket URL, upgrading http->ws and https->wss. */
export function wsUrl(path = "/ws/workout") {
  const token = getToken() ?? "";
  const base =
    API_BASE ||
    `${window.location.protocol}//${window.location.host}`;
  const url = new URL(base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = path;
  url.search = `?token=${encodeURIComponent(token)}`;
  return url.toString();
}
