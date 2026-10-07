import asyncio, os, sys
REPO=os.path.abspath(sys.argv[1]); sys.path.insert(0, REPO); os.chdir(REPO)
import numpy as np
from dyon.core.config import TwinConfig, SensorFieldSpec
from dyon.core.events import EventBus, DomainEvent
from reactive.ekf_estimator import EKFPlantStateEstimator
from reactive.health_score import HealthScoreCalculator
from intelligent.escalation_protocol import EscalationProtocol
import yaml
PROF=yaml.safe_load(open('config/sensor_profiles.yaml'))
FIELDS=["soil_moisture","soil_ec","soil_nitrogen","soil_phosphorus","soil_potassium","ndvi","pri","red_edge_slope","canopy_temperature","canopy_air_delta","fv_fm","phi_psii","ethylene","isoprene","hexenal","air_temperature","relative_humidity","co2","par"]
class TS:
    def __init__(s): s.v={}
    def get_latest(s,f): return s.v.get(f)
    def write_point(s,**k): pass
class Cache(dict):
    def set_latest(s,k,v): s[k]=v
    def get_latest_cached(s,k): return s.get(k)
class Doc:
    def __init__(s): s.events=[]
    def log_event(s,kind,payload,severity="info"): s.events.append((kind,severity))
class KG:
    def diagnose_from_readings(s,r): return []
    def diagnose(s,x): return []
class Stage:
    current_stage="anthesis"
async def main():
    cfg=TwinConfig(sensor_fields=[SensorFieldSpec(name=n) for n in FIELDS])
    bus=EventBus(); ts=TS(); cache=Cache(); doc=Doc()
    ekf=EKFPlantStateEstimator(initial_soil_moisture=0.30)
    hs=HealthScoreCalculator(cfg,bus,ts_store=ts,cache=cache,ekf=ekf)
    esc=EscalationProtocol(cfg,bus,ekf=ekf,doc_store=doc,knowledge_graph=KG(),cache=cache,seed=0)
    await hs.initialise(); await esc.initialise(); hs._stage_tracker=Stage()
    rng=np.random.default_rng(0)
    def publish_readings(bias=0.0):
        for f in FIELDS:
            band=PROF[f]["by_stage"]["anthesis"]
            ts.v[f]=band["nominal"]+rng.normal(0,PROF[f].get("noise_std",0))
        ts.v["canopy_air_delta"]+=bias
    for k in range(40):
        publish_readings(); await hs.evaluate(); await asyncio.sleep(0)
    print("nominal: confidence %.2f latched %s" % (ekf.confidence, ekf.gate_latched))
    await bus.publish(DomainEvent(event_type="reactive.escalation_requested", source_layer="reactive", source_asset=cfg.asset_id, payload={"from_state":"HEALTHY","to_state":"WATER_STRESS"}))
    await asyncio.sleep(0.05)
    for k in range(30):
        publish_readings(bias=1.5); await hs.evaluate(); await asyncio.sleep(0)
    print("after bias: confidence %.2f latched %s cache gate %s" % (ekf.confidence, ekf.gate_latched, cache.get("ekf_gate_latched")))
    await bus.publish(DomainEvent(event_type="reactive.escalation_requested", source_layer="reactive", source_asset=cfg.asset_id, payload={"from_state":"HEALTHY","to_state":"WATER_STRESS"}))
    await asyncio.sleep(0.05)
    cache.set_latest("gate_clear_requested",1.0)
    for k in range(40):
        publish_readings(); await hs.evaluate(); await asyncio.sleep(0)
    print("after clear + normal readings: confidence %.2f latched %s" % (ekf.confidence, ekf.gate_latched))
    print("audit events:", doc.events)
asyncio.run(main())
