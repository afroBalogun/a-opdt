// Talks directly to the FastAPI backend (webapp/backend/app.py) — no Next-style
// server proxy needed, CORS is enabled backend-side for this dev origin.

export interface ReadingsSnapshot {
  growth_stage: string;
  soil_moisture: number;
  soil_ec: number;
  soil_nitrogen: number;
  soil_phosphorus: number;
  soil_potassium: number;
  ndvi: number;
  pri: number;
  red_edge_slope: number;
  canopy_temperature: number;
  canopy_air_delta: number;
  fv_fm: number;
  phi_psii: number;
  ethylene: number;
  isoprene: number;
  hexenal: number;
  air_temperature: number;
  relative_humidity: number;
  co2: number;
  par: number;
}

export interface SimulationResult {
  net_assimilation: number;
  stomatal_conductance: number;
  transpiration_mm_hr: number;
  water_stress_beta: number;
  health_score: number;
  projected_state: string;
  active_categories: Record<string, string>;
}

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8500";

export async function fetchCurrentState(): Promise<ReadingsSnapshot> {
  const res = await fetch(`${API_BASE}/api/current-state`);
  if (!res.ok) throw new Error(`current-state failed: ${res.status}`);
  return res.json();
}

export async function runSimulation(snapshot: ReadingsSnapshot): Promise<SimulationResult> {
  const res = await fetch(`${API_BASE}/api/simulate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(snapshot),
  });
  if (!res.ok) throw new Error(`simulate failed: ${res.status}`);
  return res.json();
}
