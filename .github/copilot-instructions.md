## Copilot instructions for this repository

This repo is a Streamlit app and supporting scripts to estimate datacenter LCOE for hybrid Solar + BESS + Gas. The flow is: user inputs in `app.py` → resource/powerflow simulation in `core/powerflow_model.py` → financial model in `core/datacenter.py` → charts/tables in `app_components/`.

### Read this first
- UI: `app.py`, components: `app_components/st_inputs.py`, `app_components/st_outputs.py`
- Core simulation: `core/powerflow_model.py` (PVGIS weather → hourly PV → battery+generator dispatch, 20-year loop)
- Finance/LCOE: `core/datacenter.py` (Newton method to find LCOE s.t. NPV of equity cashflows ≈ 0)
- Defaults/constants: `core/defaults.py`
- Batch runs: `run_ensemble.py` (async + threads) and Streamlit page `pages/01_Ensemble_CSV.py`

### How to run (dev workflows)
- App (multi-page): run from repo root
  - streamlit run app.py
- Ensemble batch (CSV outputs in CWD):
  - python run_ensemble.py
- One-off CLI (single case):
  - python calculate_lcoe_one_shot.py --lat 31.2 --long -102.74 --solar-mw 250 --bess-mw 150 --generator-mw 100 --datacenter-load-mw 100
- Dependencies: see `requirements.txt`. The app fetches PVGIS TMY data over the network; internet is required for new locations.

### Key patterns and conventions (project-specific)
- Streamlit query params are the source of truth for inputs. `st_inputs.py` mirrors URL keys like `dc_load`, `solar`, `bess`, `gen`, etc., and keeps them in `st.session_state` for deep-linking.
- CAPEX inputs are entered as unit rates and combined into subtotals via `calculate_capex_subtotals()`. The DataCenter is then constructed with the combined unit rates:
  - `solar_capex_total_dollar_per_w`, `bess_capex_total_dollar_per_kwh`, `generator_capex_total_dollar_per_kw`, `system_integration_capex_total_dollar_per_kw`, and `soft_costs_capex_total_pct`.
- Simulation → finance handoff: `simulate_system()` returns a dict with:
  - `annual_results`: pandas DataFrame with columns like 'Solar Output - Net (MWh)', 'BESS discharged (MWh)', 'Generator Output (MWh)', 'Load Served (MWh)'.
  - `daily_sample`: pandas DataFrame with time series used for the dashboard sample week (type hints may say Polars; treat as pandas).
  Pass `annual_results` into `DataCenter(filtered_simulation_data=...)` to skip CSV-based filtering.
- Caching: `st_conditional_cache` wraps expensive functions to use `st.cache_data` only when running inside Streamlit. Avoid non-hashable arguments; large data objects are passed via parameters with a leading underscore (e.g., `_solar_ac_dataframe`) to exclude them from the cache key.
- External services: `get_solar_ac_dataframe()` pulls PVGIS TMY via `pvlib.iotools`. For ocean/invalid points, it surfaces a Streamlit warning and stops. Timezone detection uses `tzfpy`.
- Units and signs: financial values in `proforma` are in $ Millions with costs negative by convention (see `EXCLUDE_FROM_NPV`, `CALCULATE_TOTALS`). LCOE is in $/MWh.
- Generator choices and heat rates are keyed by `generator_type` ('Gas Engine' | 'Gas Turbine'), with CAPEX/OPEX pulled from `DEFAULTS_GENERATORS`.

### Data shapes and contracts (examples)
- `core.powerflow_model.simulate_system(lat, long, solar_ac_df, solar_capacity_mw, battery_power_mw, generator_capacity_mw, data_center_demand_mw=100) -> { 'annual_results': pd.DataFrame, 'daily_sample': pd.DataFrame }`
- `core.datacenter.DataCenter(..., filtered_simulation_data=pd.DataFrame) .calculate_lcoe() -> (lcoe: float, proforma: pd.DataFrame)`
- Energy mix: `calculate_energy_mix(annual_results)` returns dict with keys like `solar_to_load_twh`, `bess_to_load_twh`, `generator_twh`, `renewable_percentage`.

### Batch/ensemble specifics
- `run_ensemble.py` builds a grid of capacities and runs cases with an asyncio semaphore and `ThreadPoolExecutor` (default `MAX_CONCURRENT=10`). It saves raw results and a Pareto-filtered CSV using `core.pareto_frontier.process_ensemble_data`.
- Streamlit page `pages/01_Ensemble_CSV.py` provides the same via query params (`solar_start/stop/step`, `bess_*`, `gen_*`, `max_conc`, etc.) and prints CSV text blocks for best/raw/pareto.
- System spec strings vary by context and are for display/CSV only (powerflow uses "{solar}MW | {bess}MW | {gen}MW"; ensemble CSVs use "{solar}MW_PV_{bess}MW_BESS_{gen}MW_{type}"). Don’t rely on one format across modules.

### Gotchas
- Type hints vs reality: `daily_sample` is pandas in practice; plotting functions in `st_outputs.py` expect pandas operations.
- If you instantiate `DataCenter` without `filtered_simulation_data`, it will try to load and filter `data/powerflow_output_frozen.csv` (see `core/data_loader.py` and `SIMULATION_DATA_PATH`). The current UI always passes `filtered_simulation_data`.
- Internet connectivity is required for PVGIS fetches when exploring new locations; results may be cached per session.

### Where to add new features
- New inputs/assumptions: add to `core/defaults.py`, wire UI in `st_inputs.py`, propagate to `DataCenter` constructor and to `calculate_pro_forma` if financial.
- New charts/tables: implement in `app_components/st_outputs.py` and call them from `app.py`.
- New batch analyses: copy patterns in `run_ensemble.py` or extend `pages/01_Ensemble_CSV.py` with additional query params and result columns.
