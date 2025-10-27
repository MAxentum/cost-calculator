"""
Power flow model for simulating hybrid solar + storage + generator system performance.

It uses PVGIS data for solar resource assessment and models hour-by-hour battery flows & degradation 
over the lifetime of the project.
"""

import polars as pl
import pandas as pd
from pvlib import pvsystem, modelchain, location, iotools
import logging
import time
import streamlit as st
from typing import Dict
import numpy as np
import tzfpy
import requests
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# System constants
SYSTEM_LIFETIME_YEARS = 20
BATTERY_ROUND_TRIP_EFFICIENCY = 0.92
BATTERY_DURATION_HOURS = 4
BATTERY_DEGRADATION_PCT_PER_YEAR = 0.35 / 20  # 0.35% total over 20 years
SOLAR_DEGRADATION_PCT_PER_YEAR = 0.005  # 0.5% per year
GENERATOR_HEAT_RATE_BTU_PER_KWH = 8989.3
DC_AC_RATIO = 1.2

# PVLib configuration parameters
PVLIB_CONFIG = {
    "module_parameters": {
        "pdc0": 1,  # Normalized to 1 kW for scaling
        "gamma_pdc": -0.004,  # Temperature coefficient (%/°C)
    },
    "temperature_model_parameters": {
        "a": -3.56,  # Wind speed coefficient (°C/(W/m2))
        "b": -0.075,  # Wind speed coefficient (°C/(W/m2)/(m/s))
        "deltaT": 3,  # Temperature difference between cell and module back (°C)
    },
}


def st_conditional_cache(func):
    """Wrapper that only applies st.cache_data if running in streamlit.
    
    TODO: may want to implement a more generic cache for when not running in streamlit.
    """
    try:
        import streamlit as st
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        
        if get_script_run_ctx() is not None:
            return st.cache_data(ttl=3600)(func)
        return func
    except:
        return func


@st_conditional_cache
def get_solar_ac_dataframe(
    latitude: float,
    longitude: float,
    system_type: str = "fixed-tilt",
    surface_tilt: float = 0,
    surface_azimuth: float = 180,
) -> pd.DataFrame:
    """
    Calculate the AC output profile of a PV system based on location and configuration.

    Uses PVGIS typical meteorological year (TMY) data to simulate solar PV performance,
    accounting for temperature effects, solar angle, and system losses. The output is
    normalized to 1 MW-DC of installed capacity.

    Args:
        latitude: Site latitude in decimal degrees (positive for northern hemisphere)
        longitude: Site longitude in decimal degrees (positive for eastern hemisphere)
        system_type: Mounting system type ('fixed-tilt' or 'single-axis')
        surface_tilt: Panel tilt angle in degrees from horizontal (fixed-tilt only)
        surface_azimuth: Panel azimuth angle in degrees clockwise from north (fixed-tilt only)

    Returns:
        DataFrame containing hourly AC output values normalized to 1 MW-DC capacity

    Raises:
        ValueError: If system_type is not 'fixed-tilt' or 'single-axis'
    """
    logger.debug(
        f"Starting solar AC calculation for {latitude}, {longitude} with {system_type} system"
    )

    # Create mount based on system type
    if system_type.lower() == "fixed-tilt":
        mount = pvsystem.FixedMount(
            surface_tilt=surface_tilt, surface_azimuth=surface_azimuth
        )
    elif system_type.lower() == "single-axis":
        mount = pvsystem.SingleAxisTrackerMount()
    else:
        raise ValueError("system_type must be either 'fixed-tilt' or 'single-axis'")

    # Get timezone for the location using tzfpy
    timezone_str = tzfpy.get_tz(longitude, latitude)  # Note: tzfpy takes (lon, lat) order
    
    # Create array and location objects with timezone
    array = pvsystem.Array(mount, **PVLIB_CONFIG)
    site = location.Location(latitude, longitude)  # Removed timezone parameter

    # Create PV system with normalized 1 MW rating
    pv_system = pvsystem.PVSystem(
        arrays=[array],
        inverter_parameters={"pdc0": PVLIB_CONFIG["module_parameters"]["pdc0"]},
    )

    # Configure model chain with physical AOI model
    model = modelchain.ModelChain(
        pv_system, site, aoi_model="physical", spectral_model="no_loss"
    )

    # Fetch and process weather data
    weather_start = time.time()
    try:
        weather_data = iotools.get_pvgis_tmy(latitude, longitude)[0]
        logger.debug(f"Weather data fetch took {(time.time() - weather_start)*1000:.1f} ms")
    except requests.exceptions.HTTPError:
        st.warning("you can't pick somewhere over the sea !!!!")
        st.stop()

    # Run performance model
    model_start = time.time()
    model.run_model(weather_data)
    logger.debug(f"Model run took {(time.time() - model_start)*1000:.1f} ms")

    # Process the results
    solar_generation_df = model.results.ac.reset_index()
    
    # Convert UTC times to local timezone (timestamps are already UTC-aware)
    solar_generation_df["time_local"] = solar_generation_df["time(UTC)"].dt.tz_convert(timezone_str)

    return solar_generation_df


def simulate_battery_operation(
    df: pd.DataFrame,
    battery_capacity_mwh: float,
    initial_battery_charge: float,
    generator_capacity: float,
    load_mw: float,
    operating_year: int,
) -> pd.DataFrame:
    """
    Vectorized simulation of battery, solar and generator operation for one year.
    
    Uses numpy arrays for efficient computation of power flows and battery state.
    """
    # Calculate battery parameters with degradation
    battery_power_mw = battery_capacity_mwh / BATTERY_DURATION_HOURS
    degraded_capacity_mwh = battery_capacity_mwh * (
        1 - BATTERY_DEGRADATION_PCT_PER_YEAR * (operating_year - 1)
    )
    
    # Convert solar generation to numpy array for faster computation
    solar_generation = df["scaled_solar_generation_mw"].to_numpy()
    n_steps = len(solar_generation)
    
    # Initialize arrays
    battery_state = np.zeros(n_steps + 1)  # +1 for initial state
    battery_state[0] = initial_battery_charge
    battery_charge = np.zeros(n_steps)
    battery_discharge = np.zeros(n_steps)
    curtailed_solar = np.zeros(n_steps)
    generator_output = np.zeros(n_steps)
    unmet_load = np.zeros(n_steps)
    
    # Calculate power balance
    power_balance = solar_generation - load_mw
    excess_power = np.maximum(power_balance, 0)
    deficit_power = np.maximum(-power_balance, 0)
    
    # Vectorized simulation
    for t in range(n_steps):
        if power_balance[t] > 0:
            # Excess solar case
            available_storage = degraded_capacity_mwh - battery_state[t]
            stored_energy = min(
                min(excess_power[t], battery_power_mw),
                available_storage
            )
            battery_charge[t] = stored_energy
            curtailed_solar[t] = excess_power[t] - stored_energy
            battery_state[t + 1] = (
                battery_state[t] + stored_energy * BATTERY_ROUND_TRIP_EFFICIENCY**0.5
            )
        else:
            # Deficit case
            max_discharge = min(
                battery_power_mw,
                min(
                    deficit_power[t] / BATTERY_ROUND_TRIP_EFFICIENCY**0.5,
                    battery_state[t]
                )
            )
            battery_discharge[t] = max_discharge * BATTERY_ROUND_TRIP_EFFICIENCY**0.5
            remaining_deficit = deficit_power[t] - battery_discharge[t]
            generator_output[t] = min(remaining_deficit, generator_capacity)
            unmet_load[t] = remaining_deficit - generator_output[t]
            battery_state[t + 1] = battery_state[t] - max_discharge
    
    # Add results to DataFrame efficiently using a single assignment
    results = pd.DataFrame({
        'battery_state_mwh': battery_state[:-1],  # Exclude final state
        'battery_charge_mwh': battery_charge,
        'battery_discharge_mwh': battery_discharge,
        'curtailed_solar_mwh': curtailed_solar,
        'generator_output_mwh': generator_output,
        'unmet_load_mwh': unmet_load,
        'load_served_mwh': load_mw - unmet_load
    })
    
    return pd.concat([df, results], axis=1)


def scale_solar_generation(
    df: pd.DataFrame, installed_capacity_mw: float, operating_year: int
) -> pd.DataFrame:
    """
    Scale the normalized solar generation profile by installed capacity and degradation.

    Args:
        df: DataFrame containing normalized solar generation
        installed_capacity_mw: Installed solar capacity in MW-DC
        operating_year: Current year of operation (for degradation)

    Returns:
        DataFrame with scaled generation values
    """
    degradation_factor = 1 - SOLAR_DEGRADATION_PCT_PER_YEAR * (operating_year - 1)
    ac_capacity_mw = installed_capacity_mw / DC_AC_RATIO
    df["scaled_solar_generation_mw"] = df["p_mp"] * ac_capacity_mw * degradation_factor
    return df


@st_conditional_cache
def simulate_system(
    latitude: float,
    longitude: float,
    _solar_ac_dataframe: pd.DataFrame, # Underscore to avoid caching on this
    solar_capacity_mw: float,
    battery_power_mw: float,
    generator_capacity_mw: float,
    data_center_demand_mw: float = 100,
) -> pl.DataFrame:
    """
    Simulate complete system performance over its lifetime.

    Performs a year-by-year simulation of the hybrid power system, accounting for
    solar resource variation, battery operation, and system degradation.

    Args:
        latitude: Site latitude in decimal degrees
        longitude: Site longitude in decimal degrees
        solar_capacity_mw: Solar PV capacity in MW-DC
        battery_power_mw: Battery power capacity in MW
        generator_capacity_mw: Generator capacity in MW

    Returns:
        Polars DataFrame containing annual performance metrics for the system
    """
    logger.debug(
        f"Starting simulation for lat={latitude}, lon={longitude}, "
        f"solar={solar_capacity_mw} MW, battery={battery_power_mw} MW/"
        f"{battery_power_mw * BATTERY_DURATION_HOURS} MWh, generator={generator_capacity_mw} MW"
    )

    # Calculate battery energy capacity
    battery_capacity_mwh = battery_power_mw * BATTERY_DURATION_HOURS

    # Get normalized solar generation profile
    solar_generation_df = _solar_ac_dataframe

    annual_results = []
    for operating_year in range(1, SYSTEM_LIFETIME_YEARS + 1):
        logger.debug(f"Simulating year {operating_year} of {SYSTEM_LIFETIME_YEARS}")

        # Scale solar generation for current year
        scaled_df = scale_solar_generation(
            solar_generation_df.copy(), solar_capacity_mw, operating_year
        )

        # Set initial battery charge (empty in final year)
        initial_charge = 0 if operating_year == 0 else battery_capacity_mwh

        # Simulate battery and generator operation
        result_df = simulate_battery_operation(
            scaled_df,
            battery_capacity_mwh,
            initial_charge,
            generator_capacity_mw,
            data_center_demand_mw,
            operating_year,
        )
        # Get sample week of data for dashboard
        if operating_year == 1:
            # First get all days 182-188 (roughly July 1-7)
            sample_days_df = result_df[result_df['time_local'].dt.dayofyear.isin(range(182, 189))]
            
            # Count number of hours in each year for these days
            year_counts = sample_days_df.groupby(sample_days_df['time_local'].dt.year).size()
            # Get the year with the most data points (in case of partial years)
            best_year = year_counts.idxmax()
            
            # Take the data from the year with most complete data
            sample_week_df = sample_days_df[sample_days_df['time_local'].dt.year == best_year]
            sample_week_df = sample_week_df.reset_index(drop=True)
        
        solar_mwh_raw_tot = result_df["scaled_solar_generation_mw"].sum()
        solar_mwh_curtailed_tot = result_df["curtailed_solar_mwh"].sum()
        # Append results for the current year
        annual_results.append(
            {
                "system_spec": f"{int(solar_capacity_mw)}MW | {int(battery_power_mw)}MW | {int(generator_capacity_mw)}MW",
                "Operating Year": operating_year,
                "Solar Output - Raw (MWh)": round(solar_mwh_raw_tot),
                "Solar Output - Curtailed (MWh)": round(solar_mwh_curtailed_tot),
                "Solar Output - Net (MWh)": round(
                    solar_mwh_raw_tot - solar_mwh_curtailed_tot
                ),
                "BESS charged (MWh)": round(result_df["battery_charge_mwh"].sum()),
                "BESS discharged (MWh)": round(
                    result_df["battery_discharge_mwh"].sum()
                ),
                "Generator Output (MWh)": round(
                    result_df["generator_output_mwh"].sum()
                ),
                "Generator Fuel Input (MMBtu)": round(
                    result_df["generator_output_mwh"].sum()
                    * GENERATOR_HEAT_RATE_BTU_PER_KWH
                    / 1000
                ),
                # This method of calculating load served produces sliiightly different results to the original,
                # but I think this may be more correct.
                "Load Served (MWh)": round(
                    data_center_demand_mw * 8760 - result_df["unmet_load_mwh"].sum()
                ),
            }
        )

    logger.debug("Simulation completed successfully")
    return {
        "annual_results": pd.DataFrame(annual_results),
        "daily_sample": sample_week_df
    }

def calculate_energy_mix(simulation_data: pd.DataFrame) -> Dict[str, float]:
    """Calculate lifetime energy mix from simulation data."""
    solar_gen_net_twh = simulation_data['Solar Output - Net (MWh)'].sum() / 1_000_000
    solar_to_bess_twh = simulation_data['BESS charged (MWh)'].sum() / 1_000_000
    bess_to_load_twh = simulation_data['BESS discharged (MWh)'].sum() / 1_000_000
    generator_twh = simulation_data['Generator Output (MWh)'].sum() / 1_000_000
    total_load_twh = simulation_data['Load Served (MWh)'].sum() / 1_000_000
    
    renewable_percentage = 100 * (1 - generator_twh / total_load_twh)
    
    return {
        'solar_gen_net_twh': solar_gen_net_twh,
        'solar_to_load_twh': solar_gen_net_twh - solar_to_bess_twh,
        'bess_to_load_twh': bess_to_load_twh,
        'generator_twh': generator_twh,
        'total_generation_twh': solar_gen_net_twh + bess_to_load_twh + generator_twh,
        'total_load_twh': total_load_twh,
        'renewable_percentage': renewable_percentage
    }


if __name__ == "__main__":
    # Example simulation for El Paso, TX
    EXAMPLE_CONFIG = {
        "latitude": 31.9,
        "longitude": -106.2,
        "solar_capacity_mw": 500,
        "battery_power_mw": 100,
        "generator_capacity_mw": 100,
        "data_center_demand_mw": 100
    }

    solar_ac_dataframe = get_solar_ac_dataframe(EXAMPLE_CONFIG["latitude"], EXAMPLE_CONFIG["longitude"])
    results = simulate_system(**EXAMPLE_CONFIG, _solar_ac_dataframe=solar_ac_dataframe)
    results['annual_results'].to_csv("output_20_yrs.csv")
    print(results)
