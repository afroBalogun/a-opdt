"""L8 — Escalation Protocol: confidence-gated response to reactive-layer
stress escalations.

Per the architecture doc (section 12.5): "When anomaly classification
confidence < 60%, the Orchestrator executes a 24-hour forward simulation
before dispatching specialist agents. If confidence remains below 70%
after simulation, the case is escalated to a Plant Scientist with a full
briefing packet (symptoms, soil data, ranked diagnoses, evidence,
simulation results)."

Adaptations made here:
  - "Confidence" is the EKF's own innovation-based self-consistency signal
    (reactive/ekf_estimator.py's `confidence` property) rather than an
    anomaly classifier's output — an honest, filter-native measure, not an
    invented number.
  - No specialist agents exist yet (L7 is foundation-only per an earlier
    scoping decision), so "dispatch specialist agents" has nothing to
    dispatch to. This protocol still implements the confidence-gated
    decision structure so real dispatch can be wired in once agents exist.
  - The "24-hour forward simulation" becomes a short Monte Carlo rollout
    of the EKF's own process model (sampling its process noise Q across a
    handful of trajectories, holding the last known forcing constant as a
    simple persistence forecast) rather than a literal 24h/DSSAT run — a
    resource-appropriate proxy that still asks the doc's real question:
    does the situation look predictable, or is it still diverging?
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import numpy as np
import yaml

from dyon.core.base import LayerBase
from dyon.core.events import DomainEvent

from reactive.ekf_estimator import EKFForcing, EKFPlantStateEstimator

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import DocumentStore
    from dyon.intelligent.knowledge_graph import KnowledgeGraph

log = logging.getLogger(__name__)

_LOW_CONFIDENCE_THRESHOLD = 0.60
_ESCALATION_THRESHOLD = 0.70
_FORWARD_SIM_ROLLOUTS = 20
_FORWARD_SIM_STEPS = 5


class EscalationProtocol(LayerBase):
    layer_name = "autonomous"

    def __init__(
        self,
        config: "TwinConfig",
        event_bus: "EventBus",
        *,
        ekf: EKFPlantStateEstimator,
        doc_store: "DocumentStore",
        knowledge_graph: "KnowledgeGraph",
        profiles_path: str = "config/sensor_profiles.yaml",
    ):
        super().__init__(config, event_bus)
        self.ekf = ekf
        self.doc = doc_store
        self.kg = knowledge_graph
        self._last_forcing: EKFForcing | None = None
        # Only covers the stress categories reachable from this event's
        # payload (drought/heat_stress/frost, via soil_moisture and
        # canopy_temperature) — salinity/nutrient_deficiency/
        # photosystem_stress/pest_pressure need fields (soil_ec,
        # soil_nitrogen, fv_fm, hexenal) not published here. A full
        # diagnosis would need extending the event payload or querying
        # Influx directly; left as a known gap rather than silently
        # pretending full coverage.
        self._last_readings: dict[str, float] = {}

        with open(profiles_path) as f:
            self._profiles: dict = yaml.safe_load(f)

    async def initialise(self) -> None:
        self.bus.subscribe("reactive.escalation_requested", self._on_escalation)
        self.bus.subscribe("data_management.cycle_complete", self._on_cycle_complete)

    async def _on_cycle_complete(self, event: DomainEvent) -> None:
        if event.source_asset != self.config.asset_id:
            return
        payload = event.payload
        self._last_readings = {
            "soil_moisture": payload["soil_moisture"],
            "canopy_temperature": payload["canopy_temperature"],
            "air_temperature": payload["air_temperature"],
        }

        band = self._profiles.get("soil_moisture", {}).get("by_stage", {}).get(payload.get("growth_stage"))
        if not band:
            return
        self._last_forcing = EKFForcing(
            par_umol_m2_s=payload["par"],
            air_temp_c=payload["air_temperature"],
            canopy_temp_c=payload["canopy_temperature"],
            relative_humidity_pct=payload["relative_humidity"],
            co2_ppm=payload["co2"],
            stage_field_capacity=band["nominal"],
            stage_wilting_point=band["crit_low"],
            dt_hours=60 / 3600,
        )

    def _forward_simulate(self) -> float:
        """
        Monte Carlo rollout of the EKF's own process model. Returns a
        post-simulation confidence in [0, 1] derived from ensemble spread:
        tightly clustered trajectories mean the situation's near-term
        trajectory is predictable (higher confidence); a highly divergent
        ensemble means the model itself is unsure what happens next.
        """
        if self._last_forcing is None:
            return self.ekf.confidence

        rng = np.random.default_rng()
        final_states = []
        for _ in range(_FORWARD_SIM_ROLLOUTS):
            x = self.ekf.x.copy()
            for _ in range(_FORWARD_SIM_STEPS):
                x = self.ekf.predict_next(x, self._last_forcing)
                x = x + rng.multivariate_normal(np.zeros(len(x)), self.ekf.Q)
            final_states.append(x)

        ensemble = np.array(final_states)
        spread = ensemble.std(axis=0)
        process_std = np.sqrt(np.diag(self.ekf.Q))
        normalised_spread = float(np.mean(spread / np.maximum(process_std, 1e-9)))
        return float(max(0.0, min(1.0, 1.0 / (1.0 + normalised_spread / 10.0))))

    async def _on_escalation(self, event: DomainEvent) -> None:
        if event.source_asset != self.config.asset_id:
            return

        confidence = self.ekf.confidence
        payload = event.payload or {}
        from_state = payload.get("from_state", "?")
        to_state = payload.get("to_state", "?")

        if confidence >= _LOW_CONFIDENCE_THRESHOLD:
            self.log.info(
                "Escalation %s -> %s: confidence=%.2f, proceeding without forward simulation",
                from_state, to_state, confidence,
            )
            return

        self.log.info(
            "Escalation %s -> %s: low confidence=%.2f, running forward simulation",
            from_state, to_state, confidence,
        )
        post_sim_confidence = self._forward_simulate()

        if post_sim_confidence < _ESCALATION_THRESHOLD:
            briefing = {
                "from_state": from_state,
                "to_state": to_state,
                "initial_confidence": confidence,
                "post_simulation_confidence": post_sim_confidence,
                "ekf_state": {
                    "soil_moisture": self.ekf.soil_moisture,
                    "vcmax_eff": self.ekf.vcmax_eff,
                    "net_assimilation": self.ekf.net_assimilation,
                    "stomatal_conductance": self.ekf.stomatal_conductance,
                    "transpiration": self.ekf.transpiration,
                },
                "ekf_variances": self.ekf.variances,
                "kg_diagnosis": self.kg.diagnose(
                    self.kg.diagnose_from_readings(self._last_readings)
                ),
            }
            self.doc.log_event("escalation_human_review_required", briefing, severity="critical")
            self.log.warning(
                "Escalated to human review: %s -> %s (post-sim confidence=%.2f)",
                from_state, to_state, post_sim_confidence,
            )
        else:
            self.doc.log_event(
                "escalation_resolved_by_simulation",
                {
                    "from_state": from_state,
                    "to_state": to_state,
                    "initial_confidence": confidence,
                    "post_simulation_confidence": post_sim_confidence,
                },
                severity="warning",
            )
            self.log.info(
                "Escalation %s -> %s resolved by forward simulation (post-sim confidence=%.2f)",
                from_state, to_state, post_sim_confidence,
            )

    async def start(self) -> None:
        # Purely event-driven (subscriptions set up in initialise()) — no
        # periodic work of its own, just idles until stop() clears the flag.
        self._running = True
        self.log.info("EscalationProtocol started")
        while self._running:
            await asyncio.sleep(3600)
