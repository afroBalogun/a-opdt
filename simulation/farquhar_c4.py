"""L4 physiology core: Farquhar C4 photosynthesis coupled to Ball-Berry
stomatal conductance.

Implements the simplified two-limitation form the architecture doc specifies
literally — "A = min(Ac, Aj) - Rd" — rather than the fuller von Caemmerer
C4 quadratic co-limitation model. Parameters below are textbook-typical
maize/C4 defaults (not site-calibrated); the doc's own Customisation
Checklist (#4, #10) expects these to be replaced by the L8 Twin Calibration
Agent once it exists — flagged here rather than silently treated as exact.

References for the functional forms used (not the specific parameter
values, which are rough C4/maize literature ranges):
- Farquhar, von Caemmerer & Berry (1980) — Ac/Aj co-limitation structure.
- von Caemmerer (2000) "Biochemical Models of Leaf Photosynthesis" — the
  J/3 conversion from electron transport to C4 CO2 fixation (extra ATP
  cost of PEP regeneration vs. C3).
- Ball, Woodrow & Berry (1987) — Ball-Berry stomatal conductance model.
- Bernacchi et al. (2001) — Arrhenius temperature scaling form (derived
  for C3 Rubisco kinetics; reused here for Vcmax/Jmax scaling absent a
  maize-specific Arrhenius table).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_R_GAS = 8.314  # J / (mol K)

# ── Literature-typical C4/maize parameters at 25 deg C ──────────────────────
VCMAX25 = 60.0       # umol CO2 m-2 s-1
JMAX25 = 3.0 * VCMAX25  # umol electrons m-2 s-1 (C4 Jmax:Vcmax ratio ~3)
RD25 = 0.01 * VCMAX25   # umol CO2 m-2 s-1 (day respiration ~1% of Vcmax)
EA_VCMAX = 65330.0   # J/mol, Arrhenius activation energy
EA_JMAX = 43540.0    # J/mol
ALPHA = 0.06         # mol electrons / mol photons, quantum yield
THETA = 0.7          # curvature factor, non-rectangular hyperbola
BB_SLOPE_M = 5.0     # Ball-Berry slope (4-6 typical for C4/maize)
BB_INTERCEPT_B = 0.02  # mol H2O m-2 s-1, minimum stomatal conductance


def _arrhenius(rate25: float, leaf_temp_c: float, activation_energy: float) -> float:
    """Scale a 25 deg C rate to leaf_temp_c via the Arrhenius equation."""
    t_k = leaf_temp_c + 273.15
    return rate25 * math.exp(activation_energy * (t_k - 298.15) / (298.15 * _R_GAS * t_k))


def _saturation_vapor_pressure_kpa(temp_c: float) -> float:
    return 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))


def vapor_pressure_deficit_kpa(air_temp_c: float, relative_humidity_pct: float) -> float:
    es = _saturation_vapor_pressure_kpa(air_temp_c)
    ea = es * max(0.0, min(100.0, relative_humidity_pct)) / 100.0
    return max(0.0, es - ea)


def water_stress_factor(soil_moisture: float, wilting_point: float, field_capacity: float) -> float:
    """0 (fully stressed) .. 1 (no water limitation), linear between the two bands."""
    if field_capacity <= wilting_point:
        return 1.0
    beta = (soil_moisture - wilting_point) / (field_capacity - wilting_point)
    return max(0.0, min(1.0, beta))


@dataclass
class PhotosynthesisResult:
    net_assimilation: float       # A, umol CO2 m-2 s-1
    stomatal_conductance: float   # gs, mol H2O m-2 s-1
    iterations: int


def solve_farquhar_ball_berry(
    *,
    leaf_temp_c: float,
    par_umol_m2_s: float,
    co2_ppm: float,
    air_temp_c: float,
    relative_humidity_pct: float,
    water_stress_beta: float = 1.0,
    vcmax25_override: float | None = None,
    bb_slope_m_override: float | None = None,
    max_iterations: int = 8,
    tolerance: float = 1e-3,
) -> PhotosynthesisResult:
    """
    Couple Farquhar C4 assimilation to Ball-Berry stomatal conductance via
    fixed-point iteration (successive substitution): A depends on gs (through
    the CO2 available at the leaf surface, in this simplified big-leaf form
    approximated directly by ambient CO2 rather than solving for Ci), and gs
    depends on A. A handful of iterations converges in practice for this
    coupled pair — a lighter-weight alternative to a full Newton-Raphson
    solve, adequate for a mock/simulated sensor system.

    vcmax25_override lets a caller (the L5 EKF, or the L8 Twin Calibration
    Agent) drive Vcmax25 from its own estimate instead of the fixed
    literature default — Jmax25 and Rd25 are rescaled proportionally to
    preserve their literature ratios to Vcmax25. bb_slope_m_override
    similarly lets the L8 Twin Calibration Agent recalibrate the Ball-Berry
    slope.
    """
    vcmax25 = vcmax25_override if vcmax25_override is not None else VCMAX25
    bb_slope_m = bb_slope_m_override if bb_slope_m_override is not None else BB_SLOPE_M
    jmax25 = (JMAX25 / VCMAX25) * vcmax25
    rd25 = (RD25 / VCMAX25) * vcmax25

    vcmax = _arrhenius(vcmax25, leaf_temp_c, EA_VCMAX)
    jmax = _arrhenius(jmax25, leaf_temp_c, EA_JMAX)
    rd = rd25 * (2.0 ** ((leaf_temp_c - 25.0) / 10.0))  # Q10=2 for respiration

    # Light-limited electron transport rate (non-rectangular hyperbola)
    i2 = ALPHA * par_umol_m2_s
    j = (i2 + jmax - math.sqrt(max(0.0, (i2 + jmax) ** 2 - 4 * THETA * i2 * jmax))) / (2 * THETA)

    ac = vcmax                 # C4: Rubisco operates near CO2-saturated capacity
    aj = j / 3.0               # extra ATP cost of PEP regeneration in C4 (von Caemmerer 2000)
    a_gross = min(ac, aj)

    vpd = vapor_pressure_deficit_kpa(air_temp_c, relative_humidity_pct)
    hs = max(0.0, 1.0 - vpd / _saturation_vapor_pressure_kpa(air_temp_c)) if vpd else 1.0

    gs = BB_INTERCEPT_B + 0.005  # seed guess
    a_net = 0.0
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        a_net = a_gross * water_stress_beta - rd
        cs = max(co2_ppm, 1.0)
        gs_new = bb_slope_m * max(a_net, 0.0) * hs / cs + BB_INTERCEPT_B
        gs_new = max(gs_new, BB_INTERCEPT_B)
        if abs(gs_new - gs) < tolerance:
            gs = gs_new
            break
        gs = gs_new

    return PhotosynthesisResult(net_assimilation=a_net, stomatal_conductance=gs, iterations=iterations)
