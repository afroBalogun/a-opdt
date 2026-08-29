"""FluorescenceSensor — Fv/Fm (max PSII quantum yield), ΦPSII (effective yield).

ΦPSII is light-dependent, so it's diurnally modulated alongside PAR;
Fv/Fm (dark-adapted) is not.
"""

from __future__ import annotations

from sensing.sensors.base import BaseSensor


class FluorescenceSensor(BaseSensor):
    profile_keys = ["fv_fm", "phi_psii"]

    _PHI_PSII_DIURNAL_AMPLITUDE = -0.08  # slight midday depression (photoinhibition)

    def read(self) -> dict[str, float]:
        fv_fm = self._sample("fv_fm")
        phi_psii = self._sample(
            "phi_psii", diurnal_scale=self._PHI_PSII_DIURNAL_AMPLITUDE
        )

        return {
            "fv_fm":    self._clamp(fv_fm, 0.0, 0.83),
            "phi_psii": self._clamp(phi_psii, 0.0, 0.83),
        }
