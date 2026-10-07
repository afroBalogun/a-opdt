"""L8 — Escalation Protocol: confidence-gated response to reactive-layer
stress escalations.

Per the architecture doc (section 12.5): "When anomaly classification
confidence < 60%, the Orchestrator executes a 24-hour forward simulation
before dispatching specialist agents. If confidence remains below 70%
after simulation, the case is escalated to a Plant Scientist with a full
briefing packet (symptoms, soil data, ranked diagnoses, evidence,
simulation results)."

Adaptations made here:
  - "Confidence" is the EKF's own innovation-based self-consistency signal,
    read through `gate_confidence`, which stays at zero while the filter's
    gate is latched after a confidence collapse (reactive/ekf_estimator.py).
  - No specialist agents exist yet (L7 is foundation-only), so "dispatch"
    is logged as the case proceeding; the decision structure is in place.
  - The forward simulation is a look-ahead that is checked against
    evidence. An earlier version scored a Monte Carlo rollout by its own
    ensemble spread, which depends only on the process noise Q: it
    reported 0.86-0.90 whatever the sensors said, so no case ever reached a
    human. Now an ensemble is seeded from the filter's current state and
    covariance and propagated open-loop, with each new cycle's actual
    forcing, for _LOOKAHEAD_STEPS cycles. Each cycle the sensors' readings
    are compared with the ensemble's predicted readings (normalised
    innovation squared against ensemble spread plus sensor noise). If the
    model, started from where the filter thinks the plant is, predicts what
    the sensors then see, the situation is predictable and the case is
    resolved; if not, it goes to a human.

Outcomes, all written to the audit log:
  escalation_proceeded            gate confidence >= 0.60
  escalation_lookahead_started    gate confidence < 0.60, look-ahead running
  escalation_resolved_by_simulation   look-ahead confidence >= 0.70
  escalation_human_review_required    look-ahead confidence < 0.70, the gate
                                      is latched, or no readings arrived
  confidence_gate_latched / confidence_gate_cleared
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import numpy as np
import yaml

from dyon.core.base import LayerBase
from dyon.core.events import DomainEvent

from reactive.ekf_estimator import EKFForcing, EKFPlantStateEstimator, confidence_from_nis
from simulation.canopy_temperature import stage_delta_bounds

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig
    from dyon.core.events import EventBus
    from dyon.data.storage.base import CacheStore, DocumentStore
    from dyon.intelligent.knowledge_graph import KnowledgeGraph

log = logging.getLogger(__name__)

_LOW_CONFIDENCE_THRESHOLD = 0.60
_ESCALATION_THRESHOLD = 0.70
_LOOKAHEAD_MEMBERS = 20
_LOOKAHEAD_STEPS = 5          # cycles with readings to compare against
_LOOKAHEAD_MAX_CYCLES = 10    # give up (and refer to a human) after this many cycles

# Cache key a researcher sets (webapp POST /api/gate/clear) to re-arm a latched gate.
GATE_CLEAR_KEY = "gate_clear_requested"


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
        cache: "CacheStore | None" = None,
        profiles_path: str = "config/sensor_profiles.yaml",
        seed: int | None = None,
    ):
        super().__init__(config, event_bus)
        self.ekf = ekf
        self.doc = doc_store
        self.kg = knowledge_graph
        self.cache = cache
        self._rng = np.random.default_rng(seed)
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
        self._pending: dict | None = None
        self._was_latched = False

        with open(profiles_path) as f:
            self._profiles: dict = yaml.safe_load(f)

    async def initialise(self) -> None:
        self.bus.subscribe("reactive.escalation_requested", self._on_escalation)
        self.bus.subscribe("data_management.cycle_complete", self._on_cycle_complete)

    # ── Per-cycle bookkeeping ───────────────────────────────────────────────

    def forcing_from_payload(self, payload: dict) -> EKFForcing | None:
        band = self._profiles.get("soil_moisture", {}).get("by_stage", {}).get(payload.get("growth_stage"))
        if not band:
            return None
        delta_ww, delta_dry = stage_delta_bounds(self._profiles, payload.get("growth_stage"))
        return EKFForcing(
            par_umol_m2_s=payload["par"],
            air_temp_c=payload["air_temperature"],
            canopy_temp_c=payload["canopy_temperature"],
            relative_humidity_pct=payload["relative_humidity"],
            co2_ppm=payload["co2"],
            stage_field_capacity=band["nominal"],
            stage_wilting_point=band["crit_low"],
            dt_hours=60 / 3600,
            stage_delta_ww=delta_ww,
            stage_delta_dry=delta_dry,
        )

    async def _on_cycle_complete(self, event: DomainEvent) -> None:
        if event.source_asset != self.config.asset_id:
            return
        payload = event.payload
        self._last_readings = {
            "soil_moisture": payload["soil_moisture"],
            "canopy_temperature": payload["canopy_temperature"],
            "air_temperature": payload["air_temperature"],
        }
        forcing = self.forcing_from_payload(payload)
        if forcing is not None:
            self._last_forcing = forcing

        self._sync_gate_latch()

        if self._pending is not None and forcing is not None:
            outcome = self.lookahead_step(forcing, payload.get("soil_moisture"), payload.get("canopy_air_delta"))
            if outcome is not None:
                self._record_lookahead_outcome(outcome)

    def _sync_gate_latch(self) -> None:
        """Honour a human's clear request and log latch transitions."""
        if self.cache is not None:
            requested = self.cache.get_latest_cached(GATE_CLEAR_KEY)
            try:
                requested = float(requested or 0) > 0.5
            except (TypeError, ValueError):
                requested = False
            if requested:
                self.cache.set_latest(GATE_CLEAR_KEY, 0.0)
                if self.ekf.gate_latched:
                    self.ekf.clear_latch()
                    self.doc.log_event(
                        "confidence_gate_cleared",
                        {"confidence": self.ekf.confidence},
                        severity="info",
                    )
                    self.log.info("Confidence gate cleared by a human")

        latched = self.ekf.gate_latched
        if latched and not self._was_latched:
            self.doc.log_event(
                "confidence_gate_latched",
                {
                    "confidence": self.ekf.confidence,
                    "ekf_state": self._ekf_state(),
                    "reason": "filter confidence collapsed; a persistent sensor bias or an "
                              "unmodelled change cannot be told apart from innovations alone",
                },
                severity="critical",
            )
            self.log.warning("Confidence gate latched; autonomy suspended until cleared")
        self._was_latched = latched

    # ── Look-ahead ──────────────────────────────────────────────────────────

    def lookahead_start(self, meta: dict) -> None:
        """Seed an ensemble from the filter's current state and covariance."""
        cov = 0.5 * (self.ekf.P + self.ekf.P.T)
        members = self._rng.multivariate_normal(self.ekf.x, cov, size=_LOOKAHEAD_MEMBERS, method="eigh")
        self._pending = {"members": members, "nis": [], "dof": [], "cycles": 0, "meta": meta}

    def lookahead_step(
        self, forcing: EKFForcing, soil_moisture: float | None, canopy_air_delta: float | None
    ) -> dict | None:
        """Advance the ensemble one cycle and score it against this cycle's readings.

        Returns the outcome once enough cycles are scored (or the look-ahead
        times out), else None.
        """
        p = self._pending
        if p is None:
            return None
        p["cycles"] += 1

        q = self.ekf.Q
        members = np.array([
            self.ekf.predict_next(m, forcing) + self._rng.multivariate_normal(np.zeros(len(m)), q)
            for m in p["members"]
        ])
        p["members"] = members

        z = np.array([
            soil_moisture if soil_moisture is not None else np.nan,
            canopy_air_delta if canopy_air_delta is not None else np.nan,
        ], dtype=float)
        idx = np.where(np.isfinite(z))[0]
        if len(idx):
            predicted = np.array([self.ekf.observe(m, forcing) for m in members])[:, idx]
            spread = np.atleast_2d(np.cov(predicted, rowvar=False))
            s = spread + self.ekf.R[np.ix_(idx, idx)]
            d = z[idx] - predicted.mean(axis=0)
            p["nis"].append(float(d @ np.linalg.inv(s) @ d))
            p["dof"].append(len(idx))

        if len(p["nis"]) >= _LOOKAHEAD_STEPS:
            confidence = confidence_from_nis(float(np.mean(p["nis"])), float(np.mean(p["dof"])))
            return self._finish(confidence, "scored")
        if p["cycles"] >= _LOOKAHEAD_MAX_CYCLES:
            return self._finish(0.0, "no_readings")
        return None

    def _finish(self, confidence: float, basis: str) -> dict:
        p = self._pending
        self._pending = None
        return {
            **p["meta"],
            "post_simulation_confidence": confidence,
            "lookahead_basis": basis,
            "lookahead_cycles_scored": len(p["nis"]),
            "lookahead_mean_nis": float(np.mean(p["nis"])) if p["nis"] else None,
            "resolved": confidence >= _ESCALATION_THRESHOLD,
        }

    def _record_lookahead_outcome(self, outcome: dict) -> None:
        if outcome["resolved"]:
            self.doc.log_event("escalation_resolved_by_simulation", outcome, severity="warning")
            self.log.info(
                "Escalation %s -> %s resolved by look-ahead (confidence=%.2f)",
                outcome["from_state"], outcome["to_state"], outcome["post_simulation_confidence"],
            )
        else:
            self._refer_to_human(outcome, reason=(
                "look-ahead did not predict the sensors" if outcome["lookahead_basis"] == "scored"
                else "no readings arrived to check the look-ahead against"
            ))

    # ── Escalation entry point ──────────────────────────────────────────────

    def _ekf_state(self) -> dict:
        return {
            "soil_moisture": self.ekf.soil_moisture,
            "vcmax_eff": self.ekf.vcmax_eff,
            "net_assimilation": self.ekf.net_assimilation,
            "stomatal_conductance": self.ekf.stomatal_conductance,
            "transpiration": self.ekf.transpiration,
        }

    def _refer_to_human(self, meta: dict, reason: str) -> None:
        briefing = {
            **meta,
            "reason": reason,
            "ekf_state": self._ekf_state(),
            "ekf_variances": self.ekf.variances,
            "kg_diagnosis": self.kg.diagnose(self.kg.diagnose_from_readings(self._last_readings)),
        }
        self.doc.log_event("escalation_human_review_required", briefing, severity="critical")
        self.log.warning(
            "Escalated to human review: %s -> %s (%s)",
            meta.get("from_state"), meta.get("to_state"), reason,
        )

    async def _on_escalation(self, event: DomainEvent) -> None:
        if event.source_asset != self.config.asset_id:
            return
        payload = event.payload or {}
        meta = {
            "from_state": payload.get("from_state", "?"),
            "to_state": payload.get("to_state", "?"),
            "initial_confidence": self.ekf.gate_confidence,
            "gate_latched": self.ekf.gate_latched,
        }

        if self.ekf.gate_latched:
            self._refer_to_human(meta, reason="confidence gate latched after a collapse; "
                                              "a human must check the sensors and clear it")
            return

        if meta["initial_confidence"] >= _LOW_CONFIDENCE_THRESHOLD:
            self.doc.log_event("escalation_proceeded", meta, severity="info")
            self.log.info(
                "Escalation %s -> %s: confidence=%.2f, proceeding without look-ahead",
                meta["from_state"], meta["to_state"], meta["initial_confidence"],
            )
            return

        if self._pending is not None:
            self.log.info("Escalation %s -> %s joins the look-ahead already running",
                          meta["from_state"], meta["to_state"])
            return

        self.lookahead_start(meta)
        self.doc.log_event("escalation_lookahead_started", meta, severity="warning")
        self.log.info(
            "Escalation %s -> %s: low confidence=%.2f, look-ahead over the next %d cycles",
            meta["from_state"], meta["to_state"], meta["initial_confidence"], _LOOKAHEAD_STEPS,
        )

    async def start(self) -> None:
        # Purely event-driven (subscriptions set up in initialise()) — no
        # periodic work of its own, just idles until stop() clears the flag.
        self._running = True
        self.log.info("EscalationProtocol started")
        while self._running:
            await asyncio.sleep(3600)
