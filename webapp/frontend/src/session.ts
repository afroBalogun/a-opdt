/*
 * Accounts, roles and the two dashboard payloads.
 *
 * The token lives in localStorage so a refresh does not sign the user out. It
 * is a short-lived JWT (12 h) rather than a session cookie, which keeps the API
 * stateless and matches how the twin's other services are reached.
 */

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8500";
const TOKEN_KEY = "aopdt.token";

export type Role = "researcher" | "farmer";

export interface User {
  user_id: string;
  email: string;
  name: string;
  role: Role;
  plot_name?: string | null;
}

export interface FieldReading {
  field: string;
  label: string;
  value: number | null;
  unit: string;
  provenance: "measured" | "nominal";
  status: "ok" | "warning" | "critical" | "unknown";
  nominal: number | null;
  warn_low: number | null;
  warn_high: number | null;
  crit_low: number | null;
  crit_high: number | null;
}

export interface ResearcherDashboard {
  growth_stage: string;
  health_score: number;
  plant_state: string;
  active_categories: Record<string, string>;
  groups: Record<string, FieldReading[]>;
  measured_count: number;
  total_count: number;
  calibrated_vcmax25: number | null;
  calibrated_bb_slope_m: number | null;
}

export interface FarmerDashboard {
  growth_stage: string;
  headline: string;
  detail: string;
  action: string;
  tone: string;
  plant_state: string;
  health_score: number;
  highlights: FieldReading[];
  measured_count: number;
  total_count: number;
}

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

/** Pull the human-readable message out of FastAPI's several error shapes. */
async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) {
      // Pydantic validation errors arrive as a list of {loc, msg}.
      const first = detail[0];
      const where = Array.isArray(first?.loc) ? first.loc[first.loc.length - 1] : "";
      return where ? `${where}: ${first.msg}` : String(first.msg ?? res.statusText);
    }
  } catch {
    /* fall through to the status line */
  }
  return `${res.status} ${res.statusText}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export interface RegisterInput {
  email: string;
  password: string;
  name: string;
  role: Role;
  plot_name?: string;
}

interface AuthResponse { token: string; user: User; }

export async function register(input: RegisterInput): Promise<User> {
  const out = await request<AuthResponse>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
  // Registration returns a token, so the user goes straight to their dashboard
  // instead of being bounced to a login form they have just filled in.
  setToken(out.token);
  return out.user;
}

export async function login(email: string, password: string): Promise<User> {
  const out = await request<AuthResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setToken(out.token);
  return out.user;
}

export const me = () => request<User>("/api/auth/me");
export const fetchResearcherDashboard = () =>
  request<ResearcherDashboard>("/api/dashboard/researcher");
export const fetchFarmerDashboard = () =>
  request<FarmerDashboard>("/api/dashboard/farmer");

/* ── Advisor ──────────────────────────────────────────────────────────── */

export interface AdvisorReply { answer: string }

/**
 * Ask the twin a question. The backend decides which of its own accessors to
 * read; nothing here shapes the answer.
 */
export const askAdvisor = (question: string) =>
  request<AdvisorReply>("/api/advisor/ask", {
    method: "POST",
    body: JSON.stringify({ question }),
  });

/** Title-case an FSM state such as MULTI_STRESS for display. */
export const prettyState = (s: string) =>
  s.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());

export const prettyStage = (s: string) =>
  s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

/* ── History ──────────────────────────────────────────────────────────── */

export interface Band {
  nominal: number | null;
  warn_low: number | null;
  warn_high: number | null;
  crit_low: number | null;
  crit_high: number | null;
}

export interface Series {
  field: string;
  label: string;
  unit: string;
  window: string;
  points: { t: string; v: number }[];
  band: Band;
}

export interface FieldCatalogue {
  windows: string[];
  groups: Record<string, { field: string; label: string; unit: string }[]>;
}

export const fetchSeries = (field: string, window: string) =>
  request<Series>(`/api/history?field=${encodeURIComponent(field)}&window=${window}`);

export const fetchFieldCatalogue = () => request<FieldCatalogue>("/api/history/fields");

/* ── Escalations ──────────────────────────────────────────────────────── */

export interface Escalation {
  at?: string;
  from_state?: string;
  to_state?: string;
  severity?: string;
  confidence?: number;
  summary?: string;
  [k: string]: unknown;
}

export const fetchEscalations = () =>
  request<{ escalations: Escalation[]; count: number }>("/api/escalations");

/* ── Event log ────────────────────────────────────────────────────────── */

export interface TwinEvent {
  asset_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  severity: string;
  timestamp: string;
}

export interface EventLog {
  events: TwinEvent[];
  count: number;
  /** Matching events in the store, not just this page - the client cannot infer
   *  the end from a short page once a filter is applied. */
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
  /** False when the event store could not be reached, so the view can say so
   *  rather than showing an empty log that looks like a quiet system. */
  available: boolean;
  detail: string | null;
  types?: string[];
}

export const fetchEvents = (limit = 25, offset = 0, eventType?: string) =>
  request<EventLog>(
    `/api/events?limit=${limit}&offset=${offset}`
    + (eventType ? `&event_type=${encodeURIComponent(eventType)}` : ""),
  );

/* ── Farmer decision support ──────────────────────────────────────────── */

export interface IrrigationAdvice {
  should_irrigate: boolean;
  depth_mm: number | null;
  deficit_mm: number | null;
  daily_use_mm: number | null;
  days_of_water_left: number | null;
  best_time: string;
  reason: string;
  confidence: "measured" | "partly-modelled";
}

export interface Projection {
  horizon_hours: number;
  projected_state: string;
  projected_health: number;
  changes: string[];
  confidence: number;
  summary: string;
}

export interface StageForecast {
  current_stage: string;
  next_stage: string | null;
  gdd_accumulated: number | null;
  gdd_to_next: number | null;
  days_to_next: number | null;
  summary: string;
}

export interface Intervention {
  id: string;
  kind: string;
  note: string;
  amount: number | null;
  unit: string | null;
  logged_at: string;
  state_at_logging: string;
  health_at_logging: number;
  outcome: "pending" | "improved" | "unchanged" | "worsened";
  outcome_detail: string;
}

export const fetchIrrigation = () => request<IrrigationAdvice>("/api/farmer/irrigation");
export const fetchProjection = (hours = 48) =>
  request<Projection>(`/api/farmer/projection?hours=${hours}`);
export const fetchStageForecast = () => request<StageForecast>("/api/farmer/stage");
export const fetchInterventions = () =>
  request<{ interventions: Intervention[]; count: number }>("/api/farmer/interventions");

export const logIntervention = (body: {
  kind: string; note?: string; amount?: number | null; unit?: string | null;
}) => request<Intervention>("/api/farmer/intervention", {
  method: "POST",
  body: JSON.stringify(body),
});
