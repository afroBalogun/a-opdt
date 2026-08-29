"""L4 transpiration: simplified big-leaf Penman-Monteith.

The architecture doc's sensor stack (AS7265x, 7-in-1 RS485, DHT22, DS18B20)
has no anemometer or net-radiation sensor, so a full aerodynamic-resistance
FAO-56 Penman-Monteith isn't fittable to available inputs. Instead this
uses the standard simplified "big-leaf" form that drives transpiration
directly from stomatal conductance (already computed by the coupled
Farquhar/Ball-Berry model) and vapor pressure deficit:

    E = gs * VPD / P_atm

This is the leaf-level limit of the Penman-Monteith equation when boundary
layer resistance is neglected relative to stomatal resistance (a reasonable
simplification for a well-ventilated field canopy) — it keeps the doc's
intent (VPD-and-conductance-driven transpiration) without requiring sensors
the hardware spec doesn't include.
"""

from __future__ import annotations

_P_ATM_KPA = 101.3
_WATER_MOLAR_MASS_G = 18.015


def transpiration_mm_per_hour(stomatal_conductance_mol_m2_s: float, vpd_kpa: float) -> float:
    """gs [mol H2O m-2 s-1], VPD [kPa] -> transpiration rate [mm/hr]."""
    flux_mol_m2_s = stomatal_conductance_mol_m2_s * vpd_kpa / _P_ATM_KPA
    # mol H2O m-2 s-1 -> mm/hr: multiply by molar mass (g/mol), divide by
    # water density (1 g/cm3 = 1000 kg/m3 -> 1 mm water = 1 kg/m2 = 1000 g/m2),
    # then by seconds per hour.
    mm_per_s = flux_mol_m2_s * _WATER_MOLAR_MASS_G / 1000.0
    return mm_per_s * 3600.0
