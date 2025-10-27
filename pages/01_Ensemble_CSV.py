# ensemble_csv_app.py
import streamlit as st
import pandas as pd
import itertools
import time
import concurrent.futures as cf
from functools import lru_cache
from datetime import datetime
import logging

from core.datacenter import DataCenter
from core.powerflow_model import simulate_system, get_solar_ac_dataframe, calculate_energy_mix
from core.pareto_frontier import process_ensemble_data  # optional, used if you want Pareto too
from core.defaults import (
    DEFAULTS_SOLAR_CAPEX, DEFAULTS_BESS_CAPEX, DEFAULTS_SYSTEM_INTEGRATION_CAPEX,
    DEFAULTS_SOFT_COSTS_CAPEX, DEFAULTS_OM, DEFAULTS_FINANCIAL, DEFAULTS_GENERATORS,
    DEFAULTS_DEPRECIATION_SCHEDULE
)
from app_components.utils import sanitize_dataframe_for_streamlit, validate_case_inputs

# Configure logging to suppress websocket errors
logging.getLogger("tornado.application").setLevel(logging.CRITICAL)
logging.getLogger("tornado.general").setLevel(logging.CRITICAL)

st.set_page_config(page_title="Ensemble CSV", layout="wide")

# ---------- helpers ----------
q = st.query_params
def qi(k, d):
    try: return int(float(q.get(k, d)))
    except: return d
def qf(k, d):
    try: return float(q.get(k, d))
    except: return d
def qs(k, d):
    v = q.get(k, d)
    return v if isinstance(v, str) else str(v)

@lru_cache(maxsize=64)
def solar_ac_cached(lat_: float, lon_: float):
    return get_solar_ac_dataframe(lat_, lon_)

def df_to_csv(df: pd.DataFrame) -> str:
    return df.to_csv(index=False, lineterminator="\n")

# ---------- read fixed system/location params ----------
lat = qf("lat", 31.227493568540208)
lon = qf("long", -102.74032647817137)
dc_load = qi("dc_load", 100)
gen_type = qs("gen_type", "Gas Engine")

# ---------- read sweep ranges (exclusive stop, like Python range) ----------
solar_start = qi("solar_start", 0)
solar_stop  = qi("solar_stop", 1500)
solar_step  = qi("solar_step", 50)

bess_start  = qi("bess_start", 0)
bess_stop   = qi("bess_stop", 1500)
bess_step   = qi("bess_step", 50)

gen_start   = qi("gen_start", 0)
gen_stop    = qi("gen_stop", 125)
gen_step    = qi("gen_step", 25)

max_conc    = qi("max_conc", 10)
limit_rows  = qi("limit", 0)  # optional: cap printed rows; 0 = no cap

# ---------- read finance ----------
cost_of_debt = qf("debt_cost", DEFAULTS_FINANCIAL["cost_of_debt_pct"])
leverage      = qf("leverage", DEFAULTS_FINANCIAL["leverage_pct"])
debt_term     = qi("debt_term", DEFAULTS_FINANCIAL["debt_term_years"])
cost_of_equity = qf("equity_cost", DEFAULTS_FINANCIAL["cost_of_equity_pct"])
itc           = qf("itc", DEFAULTS_FINANCIAL["investment_tax_credit_pct"])
tax_rate      = qf("tax_rate", DEFAULTS_FINANCIAL["combined_tax_rate_pct"])
depr_sched    = DEFAULTS_DEPRECIATION_SCHEDULE

# ---------- read CAPEX unit costs ----------
# solar ($/W)
pv_modules  = qf("pv_modules",  DEFAULTS_SOLAR_CAPEX["modules"])
pv_inverters= qf("pv_inverters",DEFAULTS_SOLAR_CAPEX["inverters"])
pv_racking  = qf("pv_racking",  DEFAULTS_SOLAR_CAPEX["racking"])
pv_bos      = qf("pv_bos",      DEFAULTS_SOLAR_CAPEX["balance_of_system"])
pv_labor    = qf("pv_labor",    DEFAULTS_SOLAR_CAPEX["labor"])
# bess ($/kWh)
bess_units  = qf("bess_units",  DEFAULTS_BESS_CAPEX["units"])
bess_bos    = qf("bess_bos",    DEFAULTS_BESS_CAPEX["balance_of_system"])
bess_labor  = qf("bess_labor",  DEFAULTS_BESS_CAPEX["labor"])
# generator ($/kW)
gen_conf    = DEFAULTS_GENERATORS.get(gen_type, DEFAULTS_GENERATORS["Gas Engine"])
gensets     = qf("gensets",     gen_conf["capex"]["gensets"])
gen_bos     = qf("gen_bos",     gen_conf["capex"]["balance_of_system"])
gen_labor   = qf("gen_labor",   gen_conf["capex"]["labor"])
# system integration ($/kW)
si_microgrid= qf("si_microgrid",DEFAULTS_SYSTEM_INTEGRATION_CAPEX["microgrid"])
si_controls = qf("si_controls", DEFAULTS_SYSTEM_INTEGRATION_CAPEX["controls"])
si_labor    = qf("si_labor",    DEFAULTS_SYSTEM_INTEGRATION_CAPEX["labor"])
# soft costs (% of hard CAPEX)
soft_general   = qf("soft_general",   DEFAULTS_SOFT_COSTS_CAPEX["general_conditions"])
soft_epc       = qf("soft_epc",       DEFAULTS_SOFT_COSTS_CAPEX["epc_overhead"])
soft_design    = qf("soft_design",    DEFAULTS_SOFT_COSTS_CAPEX["design_engineering"])
soft_permit    = qf("soft_permit",    DEFAULTS_SOFT_COSTS_CAPEX["permitting"])
soft_startup   = qf("soft_startup",   DEFAULTS_SOFT_COSTS_CAPEX["startup"])
soft_insurance = qf("soft_insurance", DEFAULTS_SOFT_COSTS_CAPEX["insurance"])
soft_taxes     = qf("soft_taxes",     DEFAULTS_SOFT_COSTS_CAPEX["taxes"])

# ---------- read O&M ----------
fuel_price   = qf("fuel_price",   DEFAULTS_OM["fuel_price_dollar_per_mmbtu"])
solar_om     = qf("solar_om",     DEFAULTS_OM["solar_fixed_dollar_per_kw"])
bess_om      = qf("bess_om",      DEFAULTS_OM["bess_fixed_dollar_per_kw"])
gen_om_fixed = qf("gen_om_fixed", gen_conf["opex"]["fixed_om"])
gen_om_var   = qf("gen_om_var",   gen_conf["opex"]["variable_om"])
bos_om       = qf("bos_om",       DEFAULTS_OM["bos_fixed_dollar_per_kw_load"])
soft_om      = qf("soft_om",      DEFAULTS_OM["soft_pct"])
om_escalator = qf("om_escalator", DEFAULTS_OM["escalator_pct"])
fuel_escalator = qf("fuel_escalator", DEFAULTS_OM["fuel_escalator_pct"])

# ---------- precompute cost rates (independent of capacities) ----------
solar_rate_per_W   = pv_modules + pv_inverters + pv_racking + pv_bos + pv_labor
bess_rate_per_kWh  = bess_units + bess_bos + bess_labor
gen_rate_per_kW    = gensets + gen_bos + gen_labor
si_rate_per_kW     = si_microgrid + si_controls + si_labor
soft_rate_pct      = soft_general + soft_epc + soft_design + soft_permit + soft_startup + soft_insurance + soft_taxes

# ---------- validate inputs ----------
validation_errors = []
if dc_load <= 0:
    validation_errors.append("datacenter_load_mw must be > 0")
if solar_step <= 0:
    validation_errors.append("solar_step must be > 0")
if bess_step <= 0:
    validation_errors.append("bess_step must be > 0")
if gen_step <= 0:
    validation_errors.append("gen_step must be > 0")

if validation_errors:
    st.error("Input validation failed:\n" + "\n".join(f"- {e}" for e in validation_errors))
    st.stop()

# ---------- build all cases ----------
solar_vals = list(range(solar_start, solar_stop, solar_step))
bess_vals  = list(range(bess_start,  bess_stop,  bess_step))
gen_vals   = list(range(gen_start,   gen_stop,   gen_step))

if not solar_vals or not bess_vals or not gen_vals:
    st.error("Empty sweep - check *_start, *_stop, *_step params")
    st.stop()

cases = [
    {
        "lat": lat, "long": lon,
        "solar_pv_capacity_mw": s,
        "bess_max_power_mw": b,
        "generator_capacity_mw": g,
        "generator_type": gen_type,
        "datacenter_load_mw": dc_load,
    }
    for s, b, g in itertools.product(solar_vals, bess_vals, gen_vals)
]

# ---------- per-case compute ----------
def run_case(case: dict) -> dict:
    # First validate the case using shared utility
    validation_error = validate_case_inputs(case)
    if validation_error:
        return {**case, "system_spec": None, "lcoe": None, "renewable_percentage": None, "status": f"error: {validation_error}"}
    
    try:
        solar_df = solar_ac_cached(case["lat"], case["long"])
        pf = simulate_system(
            case["lat"], case["long"], solar_df,
            case["solar_pv_capacity_mw"], case["bess_max_power_mw"],
            case["generator_capacity_mw"], case["datacenter_load_mw"]
        )
        annual = pf["annual_results"]

        dc = DataCenter(
            solar_pv_capacity_mw=case["solar_pv_capacity_mw"],
            bess_max_power_mw=case["bess_max_power_mw"],
            generator_capacity_mw=case["generator_capacity_mw"],
            generator_type=case["generator_type"],

            solar_capex_total_dollar_per_w=solar_rate_per_W,
            bess_capex_total_dollar_per_kwh=bess_rate_per_kWh,
            generator_capex_total_dollar_per_kw=gen_rate_per_kW,
            system_integration_capex_total_dollar_per_kw=si_rate_per_kW,
            soft_costs_capex_total_pct=soft_rate_pct,

            om_solar_fixed_dollar_per_kw=solar_om,
            om_bess_fixed_dollar_per_kw=bess_om,
            om_generator_fixed_dollar_per_kw=gen_om_fixed,
            om_generator_variable_dollar_per_kwh=gen_om_var,
            fuel_price_dollar_per_mmbtu=fuel_price,
            fuel_escalator_pct=fuel_escalator,
            om_bos_fixed_dollar_per_kw_load=bos_om,
            om_soft_pct=soft_om,
            om_escalator_pct=om_escalator,

            debt_term_years=debt_term,
            leverage_pct=leverage,
            cost_of_debt_pct=cost_of_debt,
            cost_of_equity_pct=cost_of_equity,
            combined_tax_rate_pct=tax_rate,
            investment_tax_credit_pct=itc,
            depreciation_schedule=depr_sched,

            filtered_simulation_data=annual
        )

        lcoe, _ = dc.calculate_lcoe()
        mix = calculate_energy_mix(annual)
        spec = f"{case['solar_pv_capacity_mw']}MW_PV_{case['bess_max_power_mw']}MW_BESS_{case['generator_capacity_mw']}MW_{case['generator_type'].replace(' ', '')}"

        return {
            **case,
            "system_spec": spec,
            "lcoe": float(lcoe),
            "renewable_percentage": float(mix["renewable_percentage"]),
            "status": "success"
        }
    except ValueError as e:
        # Handle specific ValueError exceptions like zero energy
        error_msg = str(e).lower()
        if "zero" in error_msg and ("energy" in error_msg or "lifetime" in error_msg):
            status = "error: zero energy"
        else:
            status = f"error: {e}"
        return {**case, "system_spec": None, "lcoe": None, "renewable_percentage": None, "status": status}
    except Exception as e:
        return {**case, "system_spec": None, "lcoe": None, "renewable_percentage": None, "status": f"error: {e}"}

# ---------- run ensemble with threads ----------
t0 = time.time()
results = []
with cf.ThreadPoolExecutor(max_workers=max_conc) as ex:
    for fut in cf.as_completed([ex.submit(run_case, c) for c in cases]):
        results.append(fut.result())
elapsed = time.time() - t0

# ---------- build raw CSV ----------
raw_df = pd.DataFrame(results)
raw_df["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
cols = [
    "timestamp","lat","long","system_spec",
    "solar_pv_capacity_mw","bess_max_power_mw","generator_capacity_mw",
    "lcoe","renewable_percentage","status"
]
raw_df = raw_df[[c for c in cols if c in raw_df.columns]]

# pick best (lowest LCOE among success)
succ = raw_df[raw_df["status"] == "success"].copy()
if succ.empty:
    st.error("All cases failed")
    st.stop()

best_idx = succ["lcoe"].idxmin()
best_row = succ.loc[[best_idx]]  # keep as DF

# optional cap
raw_print_df = raw_df.head(limit_rows) if limit_rows > 0 else raw_df

# ---------- output ----------
st.markdown(f"**computed - {len(raw_df)} rows in {elapsed:.2f}s - best LCOE ${best_row.iloc[0]['lcoe']:.2f}/MWh**")

st.markdown("**best_csv**")
st.text(df_to_csv(best_row))

st.markdown("**raw_csv**")
st.text(df_to_csv(raw_print_df))

pareto_df = process_ensemble_data(results)
st.markdown("**pareto_csv**")
st.text(df_to_csv(pareto_df))