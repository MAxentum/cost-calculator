"""Script to run ensemble simulations of different system configurations."""

import asyncio
import itertools
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
import time
from concurrent.futures import ThreadPoolExecutor
import logging

from core.datacenter import DataCenter
from core.powerflow_model import simulate_system, get_solar_ac_dataframe, calculate_energy_mix
from core.pareto_frontier import process_ensemble_data
from app_components.utils import validate_case_inputs

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Suppress tornado websocket errors
logging.getLogger("tornado.application").setLevel(logging.CRITICAL)
logging.getLogger("tornado.general").setLevel(logging.CRITICAL)

MAX_CONCURRENT = 10

def run_lcoe_calculation(case: Dict[str, Any]) -> Dict[str, Any]:
    """Run the calculation synchronously."""
    # Validate inputs first using shared utility
    validation_error = validate_case_inputs(case)
    if validation_error:
        return {
            **case,
            'system_spec': None,
            'lcoe': None,
            'renewable_percentage': None,
            'status': f'error: {validation_error}'
        }
    
    try:
        # Get solar generation data
        solar_ac_dataframe = get_solar_ac_dataframe(case['lat'], case['long'])
        
        # Simulate powerflow
        powerflow_results = simulate_system(
            case['lat'],
            case['long'],
            solar_ac_dataframe,
            case['solar_pv_capacity_mw'],
            case['bess_max_power_mw'],
            case['generator_capacity_mw'],
            case['datacenter_load_mw']
        )
        
        # Create DataCenter instance with simulation data
        datacenter = DataCenter(
            solar_pv_capacity_mw=case['solar_pv_capacity_mw'],
            bess_max_power_mw=case['bess_max_power_mw'],
            generator_capacity_mw=case['generator_capacity_mw'],
            filtered_simulation_data=powerflow_results['annual_results']
        )
        
        # Calculate LCOE
        lcoe, _ = datacenter.calculate_lcoe()
        
        # Get energy mix details
        energy_mix = calculate_energy_mix(powerflow_results['annual_results'])
        
        # Create system spec string
        system_spec = f"{case['solar_pv_capacity_mw']}MW_PV_{case['bess_max_power_mw']}MW_BESS_{case['generator_capacity_mw']}MW_{case['generator_type'].replace(' ', '')}"
        
        return {
            **case,
            'system_spec': system_spec,
            'lcoe': lcoe,
            'renewable_percentage': energy_mix['renewable_percentage'],
            'status': 'success'
        }
        
    except ValueError as e:
        # Handle specific errors like zero energy
        error_msg = str(e).lower()
        if "zero" in error_msg and ("energy" in error_msg or "lifetime" in error_msg):
            status = "error: zero energy"
            logger.warning(f"Zero energy case: {case}")
        else:
            status = f"error: {e}"
            logger.error(f"ValueError in calculation: {str(e)}")
        return {
            **case,
            'system_spec': None,
            'lcoe': None,
            'renewable_percentage': None,
            'status': status
        }
    except Exception as e:
        logger.error(f"Error in calculation: {str(e)}")
        return {
            **case,
            'system_spec': None,
            'lcoe': None,
            'renewable_percentage': None,
            'status': f'error: {str(e)}'
        }

async def calculate_case(case: Dict[str, Any], case_number: int, total_cases: int, executor: ThreadPoolExecutor) -> Dict[str, Any]:
    """Calculate LCOE and renewable percentage for a single case."""
    start_time = time.time()
    
    # Create system spec string for logging
    system_spec = f"{case['solar_pv_capacity_mw']}MW_PV_{case['bess_max_power_mw']}MW_BESS_{case['generator_capacity_mw']}MW_{case['generator_type'].replace(' ', '')}"
    logger.info(f"[{case_number}/{total_cases}] Processing ({case['lat']:.2f}, {case['long']:.2f}) - {system_spec}")
    
    # Run calculation in thread pool
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, run_lcoe_calculation, case)
    
    elapsed_time = time.time() - start_time
    if result['status'] == 'success':
        logger.info(f"✓ Completed case {case_number}/{total_cases} in {elapsed_time:.2f}s - LCOE: ${result['lcoe']:.2f}/MWh, Renewable: {result['renewable_percentage']:.1f}%")
    else:
        logger.error(f"✗ Error in case {case_number}/{total_cases}: {result['status']}")
    
    return result

async def run_ensemble(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run all cases with concurrency limit."""
    total_cases = len(cases)
    logger.info(f"Starting ensemble simulation with {total_cases} cases ({MAX_CONCURRENT} concurrent)")
    logger.info("=" * 80)
    
    # Create thread pool executor
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as executor:
        # Create semaphore to limit concurrent executions
        sem = asyncio.Semaphore(MAX_CONCURRENT)
        
        async def bounded_calculate(case: Dict[str, Any], case_number: int) -> Dict[str, Any]:
            async with sem:
                return await calculate_case(case, case_number, total_cases, executor)
        
        # Run all cases
        start_time = time.time()
        tasks = [bounded_calculate(case, i+1) for i, case in enumerate(cases)]
        results = await asyncio.gather(*tasks)
        
        # Print summary
        elapsed_time = time.time() - start_time
        success_count = sum(1 for r in results if r['status'] == 'success')
        logger.info("=" * 80)
        logger.info(f"Ensemble simulation completed in {elapsed_time:.2f}s")
        logger.info(f"Successful cases: {success_count}/{total_cases}")
        logger.info("=" * 80)
        
        return results

def save_raw_results(results: List[Dict[str, Any]], output_path: Path) -> None:
    """Save results to CSV file."""
    # Convert results to DataFrame
    df = pd.DataFrame(results)
    
    # Add timestamp
    df['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Reorder columns
    columns = [
        'timestamp',
        'lat',
        'long',
        'system_spec',
        'solar_pv_capacity_mw',
        'bess_max_power_mw',
        'generator_capacity_mw',
        'lcoe',
        'renewable_percentage',
        'status'
    ]
    df = df[columns]
    
    df.to_csv(output_path, index=False)
    logger.info(f"Raw results saved to {output_path}")

async def main():
    logger.info("LCOE Solar DC Ensemble Simulation")
    logger.info("=" * 80)
    
    # Odessa, TX
    lat = 31.227493568540208
    long = -102.74032647817137
    
    ################### Define test cases ###################
    solar_capacities = list(range(0, 1500, 50))
    bess_capacities = list(range(0, 1500, 50))
    generator_capacities = list(range(0, 125, 25))

    cases = []
    # Generate all permutations
    for solar, bess, generator in itertools.product(
        solar_capacities,
        bess_capacities,
        generator_capacities,
    ):
        case = {
            'lat': lat,
            'long': long,
            'solar_pv_capacity_mw': solar,
            'bess_max_power_mw': bess,
            'generator_capacity_mw': generator,
            'generator_type': 'Gas Engine', 
            'datacenter_load_mw': 100  # Fixed load for all cases
        }
        cases.append(case)
    ################### End of test cases ###################
    
    logger.info("Configuration:")
    logger.info(f"Location: ({lat}, {long})")
    logger.info(f"Solar PV capacities: {', '.join(map(str, solar_capacities))} MW")
    logger.info(f"BESS capacities: {', '.join(map(str, bess_capacities))} MW")
    logger.info(f"Generator capacities: {', '.join(map(str, generator_capacities))} MW")
    
    # Run ensemble acrosss all points
    results = await run_ensemble(cases)
    
    # Save raw results
    output_path = Path(f"ensemble_results_raw_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv")
    save_raw_results(results, output_path)

    # Process Pareto optimal points
    pareto_points = process_ensemble_data(results)
    
    # Save Pareto optimal points
    pareto_output_path = Path(f"ensemble_results_pareto_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv")
    pareto_points.to_csv(pareto_output_path, index=False)
    logger.info(f"Saved Pareto optimal points to {pareto_output_path}")

if __name__ == "__main__":
    asyncio.run(main()) 