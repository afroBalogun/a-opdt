import type { ReadingsSnapshot } from "../api";

const GROWTH_STAGES = [
  "germination",
  "vegetative_early",
  "vegetative_late",
  "anthesis",
  "grain_fill",
  "maturity",
];

interface SliderField {
  key: keyof ReadingsSnapshot;
  label: string;
  min: number;
  max: number;
  step: number;
  unit: string;
}

const SLIDER_FIELDS: SliderField[] = [
  { key: "soil_moisture", label: "Soil moisture", min: 0.05, max: 0.4, step: 0.01, unit: "m³/m³" },
  { key: "soil_ec", label: "Soil salinity (EC)", min: 0.3, max: 4.0, step: 0.05, unit: "dS/m" },
  { key: "soil_nitrogen", label: "Soil nitrogen", min: 5, max: 80, step: 1, unit: "mg/kg" },
  { key: "soil_phosphorus", label: "Soil phosphorus", min: 2, max: 25, step: 0.5, unit: "mg/kg" },
  { key: "soil_potassium", label: "Soil potassium", min: 20, max: 180, step: 5, unit: "mg/kg" },
  { key: "air_temperature", label: "Air temperature", min: 15, max: 42, step: 0.5, unit: "°C" },
  { key: "canopy_temperature", label: "Canopy temperature", min: 15, max: 45, step: 0.5, unit: "°C" },
  { key: "relative_humidity", label: "Relative humidity", min: 10, max: 95, step: 1, unit: "%" },
  { key: "co2", label: "CO₂ concentration", min: 350, max: 500, step: 5, unit: "ppm" },
  { key: "par", label: "Light (PAR)", min: 0, max: 2000, step: 20, unit: "µmol/m²/s" },
];

// Presets adjust whichever fields realistically move together for that
// scenario — including fields not exposed as sliders (e.g. drought also
// shifts canopy_air_delta and fv_fm) — because the stress rules require
// multiple corroborating signals to move together before they trigger
// (see reactive/stress_rules.py), matching how a real event would look
// rather than a single isolated sensor changing.
const PRESETS: Record<string, Partial<ReadingsSnapshot>> = {
  "Add fertilizer": { soil_nitrogen: 65, soil_phosphorus: 20, soil_potassium: 150 },
  Irrigate: { soil_moisture: 0.35, canopy_air_delta: -1.0, fv_fm: 0.8 },
  "Heat wave": {
    air_temperature: 38,
    canopy_temperature: 40,
    relative_humidity: 30,
    isoprene: 9,
    fv_fm: 0.68,
  },
  Drought: { soil_moisture: 0.1, canopy_air_delta: 3.2, fv_fm: 0.62 },
};

interface Props {
  snapshot: ReadingsSnapshot;
  onFieldChange: (key: keyof ReadingsSnapshot, value: number) => void;
  onStageChange: (stage: string) => void;
  onPreset: (values: Partial<ReadingsSnapshot>) => void;
  onReset: () => void;
}

export function ScenarioForm({ snapshot, onFieldChange, onStageChange, onPreset, onReset }: Props) {
  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Scenario</h2>
        <button className="reset-button" onClick={onReset} type="button">
          Reset to current
        </button>
      </div>

      <label className="field">
        <span className="field-label">Growth stage</span>
        <select value={snapshot.growth_stage} onChange={(e) => onStageChange(e.target.value)}>
          {GROWTH_STAGES.map((stage) => (
            <option key={stage} value={stage}>
              {stage.replace(/_/g, " ")}
            </option>
          ))}
        </select>
      </label>

      <div className="presets">
        {Object.entries(PRESETS).map(([name, values]) => (
          <button key={name} type="button" className="preset-button" onClick={() => onPreset(values)}>
            {name}
          </button>
        ))}
      </div>

      <div className="sliders">
        {SLIDER_FIELDS.map(({ key, label, min, max, step, unit }) => (
          <label className="field slider-field" key={key}>
            <span className="field-label">
              {label}
              <span className="field-value">
                {snapshot[key]} {unit}
              </span>
            </span>
            <input
              type="range"
              min={min}
              max={max}
              step={step}
              value={snapshot[key]}
              onChange={(e) => onFieldChange(key, Number(e.target.value))}
            />
          </label>
        ))}
      </div>
    </div>
  );
}
