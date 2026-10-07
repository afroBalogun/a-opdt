"""Offline component experiments for A-PDT after the confidence-gate fixes.

Run:  python run_experiments_v2.py path/to/a-opdt  > results_v2.json
Uses the repository's own modules (physiology, canopy model, EKF, stress rules,
escalation look-ahead, calibration). Needs numpy, scipy, scikit-learn, pyyaml
and the dyon package (only its base classes are touched; no infrastructure).
"""
import json
import math
import os
import sys
from collections import deque

REPO = os.path.abspath(sys.argv[1])
sys.path.insert(0, REPO)
os.chdir(REPO)

import numpy as np
import yaml

from intelligent.escalation_protocol import EscalationProtocol
from intelligent.twin_calibration import TwinCalibrationAgent
from reactive.ekf_estimator import EKFForcing, EKFPlantStateEstimator
from reactive.health_fsm import bucket_categories_to_state
from reactive.stress_rules import evaluate_stress_rules, load_stress_rules
from simulation.canopy_temperature import expected_canopy_air_delta, stage_delta_bounds
from simulation.farquhar_c4 import solve_farquhar_ball_berry, vapor_pressure_deficit_kpa, water_stress_factor
from simulation.penman_monteith import transpiration_mm_per_hour

PROF = yaml.safe_load(open("config/sensor_profiles.yaml"))
STAGE = "anthesis"
BAND = PROF["soil_moisture"]["by_stage"][STAGE]
FC, WP = BAND["nominal"], BAND["crit_low"]
DWW, DDRY = stage_delta_bounds(PROF, STAGE)
SM_SD = PROF["soil_moisture"]["noise_std"]
DT_SD = PROF["canopy_air_delta"]["noise_std"]
DT_H = 1 / 60
N, START, SEEDS = 240, 120, 20
out = {}


def forcing_at(k):
    hour = (6 + k) % 24  # one crop hour per filter cycle
    day = max(0.0, math.sin(math.pi * (hour - 6) / 12)) if 6 <= hour <= 18 else 0.0
    tair = 24 + 8 * day
    return EKFForcing(par_umol_m2_s=1800 * day + 20, air_temp_c=tair, canopy_temp_c=tair + 0.5,
                      relative_humidity_pct=75 - 25 * day, co2_ppm=410, stage_field_capacity=FC,
                      stage_wilting_point=WP, dt_hours=DT_H, stage_delta_ww=DWW, stage_delta_dry=DDRY)


def solve(f, beta, vc=60.0):
    return solve_farquhar_ball_berry(leaf_temp_c=f.canopy_temp_c, par_umol_m2_s=f.par_umol_m2_s,
                                     co2_ppm=f.co2_ppm, air_temp_c=f.air_temp_c,
                                     relative_humidity_pct=f.relative_humidity_pct,
                                     water_stress_beta=beta, vcmax25_override=vc)


class Truth:
    """True plant + sensors. Faults switch on at START."""

    def __init__(self, seed, gs_factor=None, delta_bias=None, sm_bias=None, drought_rate=0.0,
                 spike=None, outage=None, profile_delta=False):
        self.rng = np.random.default_rng(seed)
        self.sw = FC
        self.kw = dict(gs_factor=gs_factor, delta_bias=delta_bias, sm_bias=sm_bias,
                       drought_rate=drought_rate, spike=spike, outage=outage, profile_delta=profile_delta)

    def step(self, k, f):
        kw = self.kw
        on = k >= START
        gs = solve(f, water_stress_factor(self.sw, WP, FC)).stomatal_conductance
        if kw["gs_factor"] and on:
            gs *= kw["gs_factor"]
        gs_pot = solve(f, 1.0).stomatal_conductance
        e = transpiration_mm_per_hour(gs, vapor_pressure_deficit_kpa(f.air_temp_c, f.relative_humidity_pct))
        self.sw -= e * DT_H / 300 + (kw["drought_rate"] if on else 0.0)
        z_sm = self.sw + self.rng.normal(0, SM_SD) + (kw["sm_bias"] if (kw["sm_bias"] and on) else 0.0)
        if kw["profile_delta"]:  # what the mock sensing layer publishes: stage nominal + noise
            z_dt = DWW + self.rng.normal(0, DT_SD)
        else:
            z_dt = expected_canopy_air_delta(gs, gs_pot, DWW, DDRY) + self.rng.normal(0, DT_SD)
        if kw["delta_bias"] and on:
            z_dt += kw["delta_bias"]
        if kw["spike"] and START <= k < START + kw["spike"]:
            z_dt += 1.5
        if kw["outage"] and START <= k < START + kw["outage"]:
            return None, None
        return z_sm, z_dt


def new_ekf():
    return EKFPlantStateEstimator(soil_moisture_noise_std=SM_SD, canopy_air_delta_noise_std=DT_SD,
                                  initial_soil_moisture=FC)


def new_protocol(ekf, seed):
    p = EscalationProtocol.__new__(EscalationProtocol)
    p.ekf, p._rng, p._pending = ekf, np.random.default_rng(1000 + seed), None
    return p


def run(seed, escalate=False, **kw):
    """One run. If escalate, a stress escalation is raised at the first
    post-fault cycle with gate confidence below 0.60 (or at START if none),
    and the protocol's decision path is followed to its outcome."""
    truth, ekf = Truth(seed, **kw), new_ekf()
    proto = new_protocol(ekf, seed)
    conf, gate, latched, sm_err, outcome, raised_at = [], [], [], [], None, None
    for k in range(N):
        f = forcing_at(k)
        z_sm, z_dt = truth.step(k, f)
        ekf.step(f, z_sm, z_dt)
        conf.append(ekf.confidence)
        gate.append(ekf.gate_confidence)
        latched.append(ekf.gate_latched)
        sm_err.append(ekf.soil_moisture - truth.sw)
        if escalate and outcome is None:
            if proto._pending is not None:
                res = proto.lookahead_step(f, z_sm, z_dt)
                if res is not None:
                    outcome = "resolved by look-ahead" if res["resolved"] else "human review (look-ahead)"
                    outcome_conf = res["post_simulation_confidence"]
            elif k >= START and raised_at is None and (ekf.gate_confidence < 0.60 or k == N - 1):
                raised_at = k
                if ekf.gate_latched:
                    outcome, outcome_conf = "human review (latched)", 0.0
                elif ekf.gate_confidence >= 0.60:
                    outcome, outcome_conf = "proceed", ekf.gate_confidence
                else:
                    proto.lookahead_start({"from_state": "HEALTHY", "to_state": "STRESS"})
    r = dict(conf=np.array(conf), gate=np.array(gate), latched=np.array(latched), sm_err=np.array(sm_err),
             ekf=ekf)
    if escalate:
        r.update(outcome=outcome or "none", outcome_conf=locals().get("outcome_conf"),
                 raised_at=(raised_at - START) if raised_at is not None else None)
    return r


# ── E6: the live mock convention (was: confidence 0.0 in every cycle) ─────────
res = [run(s, profile_delta=True) for s in range(SEEDS)]
c = np.array([r["conf"][20:] for r in res])
out["E6"] = dict(mean_conf=float(c.mean()), frac_below_060=float((c < 0.60).mean()),
                 runs_latched=int(sum(r["latched"].any() for r in res)))

# ── E2: accuracy and confidence under faults ─────────────────────────────────
scen = {
    "nominal": {},
    "real_drought_observed": dict(drought_rate=0.0006),
    "closure_gs_x0.7": dict(gs_factor=0.7),
    "closure_gs_x0.4": dict(gs_factor=0.4),
    "delta_bias_+0.3": dict(delta_bias=0.3),
    "delta_bias_+1.0": dict(delta_bias=1.0),
    "soil_bias_+0.05": dict(sm_bias=0.05),
    "transient_spike_2cyc": dict(spike=2),
}
E2 = {}
for name, kw in scen.items():
    rs = [run(s, **kw) for s in range(SEEDS)]
    post = [r["conf"][START:] for r in rs]
    first = [int(np.argmax(p < 0.60)) + 1 for p in post if (p < 0.60).any()]
    E2[name] = dict(
        conf_pre=float(np.mean([r["conf"][60:START].mean() for r in rs])),
        runs_below_060=len(first),
        median_cycles_to_060=float(np.median(first)) if first else None,
        conf_late=float(np.mean([r["conf"][200:].mean() for r in rs])),
        runs_latched=int(sum(r["latched"][START:].any() for r in rs)),
        gate_late=float(np.mean([r["gate"][200:].mean() for r in rs])),
        sm_rmse=float(np.mean([np.sqrt(np.mean(r["sm_err"][60:] ** 2)) for r in rs])),
    )
nom = [run(s) for s in range(SEEDS)]
E2["nominal"]["false_gate_cycles"] = int(sum((r["gate"][20:] < 0.60).sum() for r in nom))
E2["nominal"]["false_latches"] = int(sum(r["latched"].any() for r in nom))
E2["nominal"]["cycles_checked"] = int(sum(len(r["gate"][20:]) for r in nom))
out["E2"] = E2

# ── Predict-only through a 40-cycle total sensor outage ──────────────────────
o_new = [run(s, outage=40) for s in range(SEEDS)]
o_skip = []
for s in range(SEEDS):  # previous behaviour: the update was skipped, nothing propagated
    truth, ekf = Truth(s, outage=40), new_ekf()
    errs = []
    for k in range(N):
        f = forcing_at(k)
        z_sm, z_dt = truth.step(k, f)
        if z_sm is not None:
            ekf.step(f, z_sm, z_dt)
        errs.append(ekf.soil_moisture - truth.sw)
    o_skip.append(np.array(errs))
out["E2_outage"] = dict(
    sm_err_end_of_outage_new=float(np.mean([abs(r["sm_err"][START + 39]) for r in o_new])),
    sm_err_end_of_outage_skip=float(np.mean([abs(e[START + 39]) for e in o_skip])),
)
v = []
for s in range(5):
    truth, ekf = Truth(s, outage=40), new_ekf()
    for k in range(START + 40):
        f = forcing_at(k)
        z_sm, z_dt = truth.step(k, f)
        ekf.step(f, z_sm, z_dt)
        if k == START - 1:
            before = ekf.P[0, 0]
    v.append((before, ekf.P[0, 0]))
out["E2_outage"]["sm_sd_before"] = float(np.mean([math.sqrt(a) for a, _ in v]))
out["E2_outage"]["sm_sd_end_of_outage"] = float(np.mean([math.sqrt(b) for _, b in v]))
out["E2_outage"]["conf_after_outage"] = float(np.mean([r["conf"][START + 60:].mean() for r in o_new]))

# ── E3: escalation decision path ─────────────────────────────────────────────
E3 = {}
for name in ["nominal", "transient_spike_2cyc", "closure_gs_x0.7", "closure_gs_x0.4",
             "delta_bias_+1.0", "soil_bias_+0.05"]:
    rs = [run(s, escalate=True, **scen[name]) for s in range(SEEDS)]
    counts = {}
    for r in rs:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    confs = [r["outcome_conf"] for r in rs if r["outcome_conf"] is not None and "look-ahead" in r["outcome"]]
    E3[name] = dict(outcomes=counts,
                    lookahead_conf_mean=float(np.mean(confs)) if confs else None,
                    lookahead_conf_range=[float(min(confs)), float(max(confs))] if confs else None)
    # What happens to an escalation raised later, once the gate has latched.
    late = []
    for s in range(SEEDS):
        r = run(s, **scen[name])
        late.append("human review (latched)" if r["latched"][-1]
                    else ("proceed" if r["gate"][-1] >= 0.60 else "look-ahead"))
    E3[name]["late_escalation"] = {o: late.count(o) for o in set(late)}
out["E3"] = E3

# ── E4: rule engine with merged overrides ────────────────────────────────────
rules = load_stress_rules("config/stress_thresholds.yaml")
base = {f: PROF[f]["by_stage"][STAGE]["nominal"] for f in PROF if "by_stage" in PROF[f]}


def ev(stage, **chg):
    rd = dict(base)
    rd.update(chg)
    res = evaluate_stress_rules(rules, rd, stage)
    return dict(stage=stage, change=chg, active={k: v for k, v in res.items() if v},
                state=bucket_categories_to_state(res))


out["E4"] = [
    ev("anthesis"),
    ev("vegetative_late", soil_moisture=0.19),
    ev("vegetative_late", soil_moisture=0.19, canopy_air_delta=1.3),
    ev("anthesis", soil_moisture=0.21),
    ev("anthesis", soil_moisture=0.21, canopy_air_delta=0.9),
    ev("vegetative_late", soil_moisture=0.21, canopy_air_delta=0.9),
    ev("vegetative_late", soil_moisture=0.12, canopy_air_delta=3.0),
    ev("vegetative_late", soil_moisture=0.12, canopy_air_delta=3.0, fv_fm=0.66),
    ev("anthesis", canopy_temperature=34.0),
    ev("anthesis", canopy_temperature=34.0, isoprene=12.0),
    ev("vegetative_late", canopy_temperature=34.0, isoprene=12.0),
    ev("anthesis", soil_moisture=0.21, canopy_air_delta=0.9, soil_ec=2.0),
    ev("anthesis", hexenal=5.0),
    ev("anthesis", hexenal=5.0, ethylene=2.0),
    ev("anthesis", soil_moisture=0.14, canopy_air_delta=2.6),
    ev("anthesis", soil_moisture=0.14, canopy_air_delta=2.6, fv_fm=0.66),
]


# ── E5: calibration with plausible ranges ────────────────────────────────────
def make_buffer(vc, m, seed, n=60):
    rng, buf = np.random.default_rng(seed), []
    for k in range(n):
        f = forcing_at(k * 7)
        sw = rng.uniform(0.18, 0.30)
        r = solve_farquhar_ball_berry(leaf_temp_c=f.canopy_temp_c, par_umol_m2_s=f.par_umol_m2_s, co2_ppm=410,
                                      air_temp_c=f.air_temp_c, relative_humidity_pct=f.relative_humidity_pct,
                                      water_stress_beta=water_stress_factor(sw, WP, FC),
                                      vcmax25_override=vc, bb_slope_m_override=m)
        buf.append(dict(growth_stage=STAGE, soil_moisture=sw, canopy_temperature=f.canopy_temp_c,
                        par=f.par_umol_m2_s, co2=410, air_temperature=f.air_temp_c,
                        relative_humidity=f.relative_humidity_pct,
                        ekf_net_assimilation=r.net_assimilation + rng.normal(0, 0.5),
                        ekf_stomatal_conductance=r.stomatal_conductance + rng.normal(0, 0.005)))
    return buf


E5 = []
for vc_t, m_t in [(45.0, 4.0), (60.0, 5.0), (75.0, 6.0)]:
    rows = []
    for s in range(10):
        a = TwinCalibrationAgent.__new__(TwinCalibrationAgent)
        a._profiles, a._buffer, a.last_ranges = PROF, deque(make_buffer(vc_t, m_t, s), maxlen=60), {}
        np.random.seed(s)
        vc, m, obj = a._calibrate()
        g = a.last_ranges
        rows.append((vc, m, g["vcmax25_low"], g["vcmax25_high"], g["bb_slope_m_low"], g["bb_slope_m_high"]))
    rows = np.array(rows)
    E5.append(dict(true=(vc_t, m_t), vc_mean=float(rows[:, 0].mean()), m_mean=float(rows[:, 1].mean()),
                   vc_range_width=float((rows[:, 3] - rows[:, 2]).mean()),
                   m_range_width=float((rows[:, 5] - rows[:, 4]).mean()),
                   vc_truth_in_range=int(((rows[:, 2] <= vc_t) & (vc_t <= rows[:, 3])).sum()),
                   m_truth_in_range=int(((rows[:, 4] <= m_t) & (m_t <= rows[:, 5])).sum())))
out["E5"] = E5

print(json.dumps(out, indent=1, default=float))
