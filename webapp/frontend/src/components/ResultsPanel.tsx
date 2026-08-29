import type { SimulationResult } from "../api";

const STATE_COLORS: Record<string, string> = {
  HEALTHY: "#3f7a4e",
  WATER_STRESS: "#c08a2e",
  NUTRIENT_DEFICIT: "#c08a2e",
  SALINITY_STRESS: "#c08a2e",
  CHLOROPHYLL_STRESS: "#c08a2e",
  MULTI_STRESS: "#a33b2e",
};

function scoreColor(score: number): string {
  if (score >= 85) return "#3f7a4e";
  if (score >= 50) return "#c08a2e";
  return "#a33b2e";
}

interface Props {
  result: SimulationResult | null;
  loading: boolean;
}

export function ResultsPanel({ result, loading }: Props) {
  return (
    <div className="panel results-panel">
      <div className="panel-header">
        <h2>Projected plant state</h2>
        {loading && <span className="loading-dot" aria-label="updating" />}
      </div>

      {!result ? (
        <p className="muted">Loading baseline…</p>
      ) : (
        <>
          <div className="state-badge" style={{ background: STATE_COLORS[result.projected_state] ?? "#666" }}>
            {result.projected_state.replace(/_/g, " ")}
          </div>

          <div className="gauge">
            <div className="gauge-label">
              <span>Health score</span>
              <span className="gauge-value">{result.health_score.toFixed(1)}</span>
            </div>
            <div className="gauge-track">
              <div
                className="gauge-fill"
                style={{
                  width: `${Math.max(0, Math.min(100, result.health_score))}%`,
                  background: scoreColor(result.health_score),
                }}
              />
            </div>
          </div>

          <div className="metric-grid">
            <div className="metric">
              <span className="metric-label">Net photosynthesis (A)</span>
              <span className="metric-value">{result.net_assimilation.toFixed(2)} µmol CO₂/m²/s</span>
            </div>
            <div className="metric">
              <span className="metric-label">Stomatal conductance (gs)</span>
              <span className="metric-value">{result.stomatal_conductance.toFixed(3)} mol/m²/s</span>
            </div>
            <div className="metric">
              <span className="metric-label">Transpiration (E)</span>
              <span className="metric-value">{result.transpiration_mm_hr.toFixed(3)} mm/hr</span>
            </div>
            <div className="metric">
              <span className="metric-label">Water-stress factor</span>
              <span className="metric-value">{result.water_stress_beta.toFixed(2)}</span>
            </div>
          </div>

          {Object.keys(result.active_categories).length > 0 && (
            <div className="active-categories">
              <span className="field-label">Active stress signals</span>
              <ul>
                {Object.entries(result.active_categories).map(([category, severity]) => (
                  <li key={category}>
                    <strong>{category.replace(/_/g, " ")}</strong> — {severity}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}
