"""Expected canopy-air temperature difference (Tc - Ta) from stomatal conductance.

Follows the crop water stress index idea (Idso et al., 1981; Jackson et al.,
1981): the canopy-air difference of a crop sits between a well-watered
baseline and a non-transpiring upper limit, in proportion to how far its
transpiration has fallen below what an unstressed plant would manage under
the same weather. Conductance stands in for transpiration here:

    dT = dT_ww + (dT_dry - dT_ww) * (1 - gs / gs_potential)

gs_potential is the coupled Farquhar/Ball-Berry conductance with no water
stress (beta = 1) under the same forcing. The two bounds come from
config/sensor_profiles.yaml's canopy_air_delta band for the current stage:
the nominal is the well-watered baseline and crit_high the dry limit.

Why this replaced the earlier proxy
-----------------------------------
The previous expression, k / gs with k = 0.05, can only be positive, while
the sensing layer (and real well-watered canopies) report Tc - Ta below zero:
-1.2 C nominal at anthesis. The filter's innovation on this channel therefore
never closed, which pinned EKF confidence at zero during nominal operation.
This form matches the sensor convention by construction: an unstressed plant
is expected at the stage nominal, day or night.
"""

from __future__ import annotations

# Used only when a stage has no canopy_air_delta band in the profiles.
DEFAULT_DELTA_WW = -1.0
DEFAULT_DELTA_DRY = 3.0


def relative_conductance(gs: float, gs_potential: float) -> float:
    """gs as a fraction of its unstressed value, clipped to [0, 1]."""
    if gs_potential <= 0:
        return 1.0
    return max(0.0, min(1.0, gs / gs_potential))


def expected_canopy_air_delta(
    gs: float,
    gs_potential: float,
    delta_ww: float = DEFAULT_DELTA_WW,
    delta_dry: float = DEFAULT_DELTA_DRY,
) -> float:
    """Tc - Ta [deg C] expected for conductance gs against its unstressed value."""
    return delta_ww + (delta_dry - delta_ww) * (1.0 - relative_conductance(gs, gs_potential))


def stage_delta_bounds(profiles: dict, stage: str | None) -> tuple[float, float]:
    """(well-watered, dry) canopy-air bounds for a stage from sensor_profiles.yaml."""
    band = profiles.get("canopy_air_delta", {}).get("by_stage", {}).get(stage or "") or {}
    return (
        float(band.get("nominal", DEFAULT_DELTA_WW)),
        float(band.get("crit_high", DEFAULT_DELTA_DRY)),
    )
