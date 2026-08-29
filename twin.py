"""
A-OPDT Twin Entry Point — Stage 4 (Autonomous Twin, partial)
───────────────────────────────────────────────────
Wires together all eight ADADT layers for the maize digital twin (L7's
individual agents and L8's RL policy excepted — see class docstring),
using mock sensors in place of real hardware.

Run with:
    python twin.py
or:
    dtforge run twin
"""

from __future__ import annotations

import asyncio
import logging
import os
import yaml

from pathlib import Path

from dotenv import load_dotenv

# Which env file backs this run. Unset means .env and everything on localhost;
# A_OPDT_ENV_FILE=.env.cloud points every layer at managed cloud services.
ENV_FILE = Path(__file__).with_name(os.getenv("A_OPDT_ENV_FILE", ".env"))

# dyon's TwinConfig reads the env file through pydantic-settings, which
# populates the config object but never os.environ. Plain os.getenv flags
# (A_OPDT_PHYSICAL_POD below) would silently miss anything set there, so load it
# properly first. Anchored to this file rather than the working directory, so
# both behave the same however the twin is launched.
load_dotenv(ENV_FILE)

from dyon.core.base import AbstractDigitalTwin, LayerBase
from dyon.core.config import SensorFieldSpec, TwinConfig
from dyon.core.lifecycle import TwinLifecycle

from intelligent.escalation_protocol import EscalationProtocol
from intelligent.knowledge_graph_spec import build_maize_kg_spec
from intelligent.twin_calibration import TwinCalibrationAgent
from reactive.ekf_estimator import EKFPlantStateEstimator
from reactive.health_fsm import PlantHealthFSM
from reactive.health_score import HealthScoreCalculator
from sensing.mock_publisher import MockSensorPublisher
from simulation.biotic_pod_dt import BioticPodDT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
log = logging.getLogger("a_opdt")

# Field names published by the mock sensor stack (config/sensor_profiles.yaml).
# Populates TwinConfig.sensor_fields so Ditto sync, health scoring, and the
# reactive layer have a field list to read from Influx/Redis.
SENSOR_FIELD_NAMES = [
    "soil_moisture", "soil_ec", "soil_nitrogen", "soil_phosphorus", "soil_potassium",
    "ndvi", "pri", "red_edge_slope",
    "canopy_temperature", "canopy_air_delta",
    "fv_fm", "phi_psii",
    "ethylene", "isoprene", "hexenal",
    "air_temperature", "relative_humidity", "co2", "par",
]


class MaizeTwin(AbstractDigitalTwin):
    """
    A-OPDT twin: mock sensing → InfluxDB → MQTT → Ditto state sync (L1-L3),
    the Biotic Pod DT (L4), health scoring + EKF (L5), the 8-state Plant
    Health FSM (L6), the L7 foundation (Neo4j knowledge graph +
    Orchestrator skeleton — no individual TwinAgents yet, deferred pending
    a real LLM API key / trained ML models), and L8's Twin Calibration
    Agent + Escalation Protocol (PPO RL irrigation policy deferred — a
    separate, larger scoping decision).
    """

    def build_layers(self) -> dict[str, LayerBase]:
        # ── Data layer: storage adapters ────────────────────────────────────
        from dyon.data.storage.influx import InfluxAdapter
        from dyon.data.storage.mongo import MongoAdapter
        from dyon.data.storage.redis_store import RedisAdapter
        from dyon.network.transport import MQTTTransport

        ts_store = InfluxAdapter(self.config)
        doc_store = MongoAdapter(self.config)
        cache = RedisAdapter(self.config)
        mqtt_transport = MQTTTransport(self.config)
        mqtt_transport.connect()

        # Stash for reuse by later-stage layers (reactive, simulation, etc.)
        self.ts_store = ts_store
        self.doc_store = doc_store
        self.cache = cache
        self.mqtt_transport = mqtt_transport

        layers: dict[str, LayerBase] = {}

        # ── Sensing / Network layer (mock, optionally overlaid) ─────────────
        # A_OPDT_PHYSICAL_POD subscribes to the pod node's topic and replaces
        # the three fields it measures. Unset, this layer stays fully mock.
        physical_overlay = None
        if os.getenv("A_OPDT_PHYSICAL_POD", "false").lower() == "true":
            from sensing.physical_overlay import PhysicalSensorOverlay

            physical_overlay = PhysicalSensorOverlay(mqtt_transport)
            physical_overlay.start()
            self.physical_overlay = physical_overlay

        layers["sensing"] = MockSensorPublisher(
            self.config,
            self.bus,
            ts_store=ts_store,
            mqtt_transport=mqtt_transport,
            cache=cache,
            phenology_path="config/maize_phenology.yaml",
            profiles_path="config/sensor_profiles.yaml",
            physical_overlay=physical_overlay,
        )

        # ── Service layer: Ditto state sync + FastAPI ───────────────────────
        from dyon.services.ditto import DittoSyncService
        from dyon.services.ditto.client import DittoClient

        ditto_client = DittoClient(self.config)
        self.ditto_client = ditto_client

        layers["service_ditto"] = DittoSyncService(
            self.config,
            self.bus,
            ts_store=ts_store,
            cache=cache,
            ditto_client=ditto_client,
        )

        # ── Simulation/Model layer (L4): Biotic Pod DT ──────────────────────
        layers["simulation"] = BioticPodDT(
            self.config,
            self.bus,
            ts_store=ts_store,
            cache=cache,
            profiles_path="config/sensor_profiles.yaml",
        )

        # Constructed here (not inside HealthScoreCalculator) so the L8
        # escalation protocol can read the same live filter state.
        with open("config/sensor_profiles.yaml") as f:
            _profiles_for_ekf = yaml.safe_load(f)
        _soil_moisture_profile = _profiles_for_ekf.get("soil_moisture", {})
        _canopy_delta_profile = _profiles_for_ekf.get("canopy_air_delta", {})
        _germination_band = _soil_moisture_profile.get("by_stage", {}).get("germination", {})
        ekf = EKFPlantStateEstimator(
            soil_moisture_noise_std=_soil_moisture_profile.get("noise_std", 0.005),
            canopy_air_delta_noise_std=_canopy_delta_profile.get("noise_std", 0.10),
            initial_soil_moisture=_germination_band.get("nominal", 0.28),
        )
        self.ekf = ekf

        # ── Data Management layer (L5): smoothing + health scoring ─────────
        layers["data_management"] = HealthScoreCalculator(
            self.config,
            self.bus,
            ts_store=ts_store,
            cache=cache,
            ekf=ekf,
            profiles_path="config/sensor_profiles.yaml",
        )

        # ── Reactive layer (L6): 8-state Plant Health FSM ───────────────────
        layers["reactive"] = PlantHealthFSM(
            self.config,
            self.bus,
            ts_store=ts_store,
            cache=cache,
            doc_store=doc_store,
            stress_rules_path="config/stress_thresholds.yaml",
        )

        # ── Intelligent layer (L7): Neo4j KG + Orchestrator skeleton ────────
        # Foundation only for now — no individual TwinAgents yet (deferred:
        # several of the doc's 18 agents need either a real LLM API key or
        # trained ML models this mock system has no data to produce).
        from neo4j import GraphDatabase

        from dyon.intelligent.knowledge_graph import KnowledgeGraph
        from dyon.intelligent.mas import MultiAgentSystem

        neo4j_driver = GraphDatabase.driver(
            self.config.neo4j.uri,
            auth=(self.config.neo4j.user, self.config.neo4j.password),
        )
        knowledge_graph = KnowledgeGraph(self.config, neo4j_driver)
        knowledge_graph.setup_from_spec(build_maize_kg_spec("config/stress_thresholds.yaml"))
        self.knowledge_graph = knowledge_graph

        layers["intelligent"] = MultiAgentSystem(
            self.config,
            self.bus,
            agents=[],
            cache=cache,
            doc_store=doc_store,
        )

        # ── Autonomous layer (L8): Twin Calibration + Escalation Protocol ───
        # PPO RL irrigation policy deferred (separate scoping decision) —
        # these two are the self-contained, verifiable-this-session pieces.
        layers["autonomous_calibration"] = TwinCalibrationAgent(
            self.config,
            self.bus,
            cache=cache,
            doc_store=doc_store,
            profiles_path="config/sensor_profiles.yaml",
        )
        layers["autonomous_escalation"] = EscalationProtocol(
            self.config,
            self.bus,
            ekf=ekf,
            doc_store=doc_store,
            knowledge_graph=knowledge_graph,
            profiles_path="config/sensor_profiles.yaml",
        )

        return layers


async def main() -> None:
    config = TwinConfig(
        _env_file=ENV_FILE,
        sensor_fields=[SensorFieldSpec(name=n) for n in SENSOR_FIELD_NAMES],
    )
    log.info("config from %s", ENV_FILE.name)
    log.info(
        "Starting A-OPDT twin — asset_id=%s asset_type=%s",
        config.asset_id,
        config.asset_type,
    )

    twin = MaizeTwin(config)
    lifecycle = TwinLifecycle()
    lifecycle.add(twin)

    await lifecycle.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
