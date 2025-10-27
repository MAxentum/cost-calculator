""" Datacenter class for LCOE calculations and proforma generation."""

import pandas as pd
from typing import Tuple
from dataclasses import dataclass, field
from core.data_loader import load_simulation_data
from core.defaults import (
    DATACENTER_DEMAND_MW,
    SIMULATION_DATA_PATH,
    GENERATOR_HEAT_RATES,
    DEFAULTS_SOLAR_CAPEX,
    DEFAULTS_BESS_CAPEX,
    DEFAULTS_SYSTEM_INTEGRATION_CAPEX,
    DEFAULTS_SOFT_COSTS_CAPEX,
    DEFAULTS_OM,
    DEFAULTS_FINANCIAL,
    DEFAULTS_DEPRECIATION_SCHEDULE,
    DEFAULTS_GENERATORS,
    BESS_HRS_STORAGE
)

# Parameters for LCOE optimization
LCOE_OPT_LOWER_BOUND = 50
LCOE_OPT_UPPER_BOUND = 300
LCOE_OPT_TOLERANCE = 0.0001
LCOE_OPT_MAX_ITERATIONS = 10000

# Columns to read in from powerflow model
POWERFLOW_COLUMNS_TO_ASSIGN = [
    'Solar Output - Net (MWh)',  # After inverter losses & curtailment
    'BESS charged (MWh)',  # Amount that the BESS is charged by the solar PV
    'BESS discharged (MWh)',  # Amount that the BESS is discharged to the load
    'Generator Output (MWh)',
    'Load Served (MWh)'
]

# Columns to exclude from NPV calculations
EXCLUDE_FROM_NPV = [
    'Fuel Unit Cost', 'Solar Fixed O&M Rate', 'Battery Fixed O&M Rate',
    'Generator Fixed O&M Rate', 'Generator Variable O&M Rate', 'BOS Fixed O&M Rate',
    'Soft O&M Rate', 'LCOE', 'Debt Outstanding, Yr Start', 'Depreciation Schedule'
]

# Columns to calculate lifetime totals for
CALCULATE_TOTALS = [
    'Solar Output - Net (MWh)', 'BESS discharged (MWh)', 'Generator Output (MWh)',
    'Generator Fuel Input (MMBtu)', 'Load Served (MWh)'
]


@dataclass
class DataCenter:
    """Represents an off-grid datacenter energy system with solar, BESS, and generator."""
    
    # Required configuration inputs
    solar_pv_capacity_mw: int
    bess_max_power_mw: int
    generator_capacity_mw: int
    
    # System constants with defaults
    generator_type: str = 'Gas Engine'
    datacenter_load_mw: int = DATACENTER_DEMAND_MW
    bess_hrs_storage: int = BESS_HRS_STORAGE

    # CAPEX rate subtotals
    solar_capex_total_dollar_per_w: float = sum(DEFAULTS_SOLAR_CAPEX.values())
    bess_capex_total_dollar_per_kwh: float = sum(DEFAULTS_BESS_CAPEX.values())
    generator_capex_total_dollar_per_kw: float = sum(DEFAULTS_GENERATORS['Gas Engine']['capex'].values())
    system_integration_capex_total_dollar_per_kw: float = sum(DEFAULTS_SYSTEM_INTEGRATION_CAPEX.values())
    soft_costs_capex_total_pct: float = sum(DEFAULTS_SOFT_COSTS_CAPEX.values())
    
    # O&M inputs
    fuel_price_dollar_per_mmbtu: float = DEFAULTS_OM['fuel_price_dollar_per_mmbtu']
    fuel_escalator_pct: float = DEFAULTS_OM['fuel_escalator_pct']
    om_generator_fixed_dollar_per_kw: float = DEFAULTS_GENERATORS['Gas Engine']['opex']['fixed_om']
    om_generator_variable_dollar_per_kwh: float = DEFAULTS_GENERATORS['Gas Engine']['opex']['variable_om']
    om_solar_fixed_dollar_per_kw: float = DEFAULTS_OM['solar_fixed_dollar_per_kw']
    om_bess_fixed_dollar_per_kw: float = DEFAULTS_OM['bess_fixed_dollar_per_kw']
    om_bos_fixed_dollar_per_kw_load: float = DEFAULTS_OM['bos_fixed_dollar_per_kw_load']
    om_soft_pct: float = DEFAULTS_OM['soft_pct']
    om_escalator_pct: float = DEFAULTS_OM['escalator_pct']
    
    # Financial inputs
    depreciation_schedule: list = field(default_factory=lambda: DEFAULTS_DEPRECIATION_SCHEDULE)
    investment_tax_credit_pct: float = DEFAULTS_FINANCIAL['investment_tax_credit_pct']
    cost_of_debt_pct: float = DEFAULTS_FINANCIAL['cost_of_debt_pct']
    leverage_pct: float = DEFAULTS_FINANCIAL['leverage_pct']
    debt_term_years: int = DEFAULTS_FINANCIAL['debt_term_years']
    cost_of_equity_pct: float = DEFAULTS_FINANCIAL['cost_of_equity_pct']
    combined_tax_rate_pct: float = DEFAULTS_FINANCIAL['combined_tax_rate_pct']
    construction_time_years: int = DEFAULTS_FINANCIAL['construction_time_years']

    # Optional: simulation data can be passed in if already in memory
    full_simulation_data: pd.DataFrame = None
    filtered_simulation_data: pd.DataFrame = None
    location: str = None
    
    def __post_init__(self):
        if self.full_simulation_data is None and self.filtered_simulation_data is None:
            self.full_simulation_data = load_simulation_data(SIMULATION_DATA_PATH)

        if self.filtered_simulation_data is None:
            self._filter_simulation_data()
    
    def _filter_simulation_data(self) -> None:
        """Filter simulation data based on configuration."""
        # Create system spec string and extract relevant case
        system_spec = f"{int(self.solar_pv_capacity_mw)}MW | {int(self.bess_max_power_mw)}MW | {int(self.generator_capacity_mw)}MW"
        self.filtered_simulation_data = self.full_simulation_data[
            (self.full_simulation_data['Location'].str.strip() == self.location.strip()) &
            (self.full_simulation_data['System Spec'] == system_spec)
        ]
        
        if self.filtered_simulation_data.empty:
            raise ValueError(
                f"No matching simulation data found for configuration:\n"
                f"Location: {self.location}\n"
                f"System Spec: {system_spec}"
            )

    def calculate_pro_forma(self, lcoe: float) -> pd.DataFrame:
        """Calculate the proforma financial model for a given LCOE."""
        years = list(range(-1, 21))
        proforma = pd.DataFrame(index=years)
        proforma.index.name = 'Year'
        
        # Populate operating years with powerflow model outputs
        for year in self.filtered_simulation_data['Operating Year'].unique():
            year_data = self.filtered_simulation_data[self.filtered_simulation_data['Operating Year'] == year].iloc[0]
            proforma.loc[year, 'Operating Year'] = year
            for column in POWERFLOW_COLUMNS_TO_ASSIGN:
                proforma.loc[year, column] = year_data[column]
            
            # Calculate fuel input based on generator type and output
            heat_rate = GENERATOR_HEAT_RATES[self.generator_type]
            generator_output_kwh = proforma.loc[year, 'Generator Output (MWh)'] * 1000
            proforma.loc[year, 'Generator Fuel Input (MMBtu)'] = (
                generator_output_kwh * heat_rate / 1_000_000  # Convert BTU to MMBtu
            )

        # Calculate financial totals (all values in $M)
        solar_capex = self.solar_capex_total_dollar_per_w * self.solar_pv_capacity_mw
        bess_capex = self.bess_capex_total_dollar_per_kwh * self.bess_max_power_mw * self.bess_hrs_storage / 1000
        generator_capex = self.generator_capex_total_dollar_per_kw * self.generator_capacity_mw / 1_000
        system_integration_capex = self.system_integration_capex_total_dollar_per_kw * self.datacenter_load_mw / 1_000
        
        total_hard_capex = solar_capex + bess_capex + generator_capex + system_integration_capex
        soft_costs = total_hard_capex * (self.soft_costs_capex_total_pct / 100)
        total_capex = total_hard_capex + soft_costs
        total_debt = total_capex * (self.leverage_pct / 100)
        interest_rate = self.cost_of_debt_pct / 100
        
        # Calculate fixed debt service payment
        fixed_debt_payment = (total_debt * interest_rate * (1 + interest_rate)**self.debt_term_years / 
                            ((1 + interest_rate)**self.debt_term_years - 1))
        
        # Calculate Federal Investment Tax Credit amount
        renewable_proportion_of_hard_capex = (solar_capex + bess_capex) / total_hard_capex
        tax_credit_amount = total_capex * renewable_proportion_of_hard_capex * (self.investment_tax_credit_pct / 100)
        amount_that_is_depreciable = total_capex - tax_credit_amount / 2

        # Initialize debt & ITC values for year 1
        proforma.loc[1, 'Debt Outstanding, Yr Start'] = total_debt
        proforma.loc[1, 'Federal ITC'] = tax_credit_amount

        ###### CONSTRUCTION PERIOD ######
        construction_years = range(-self.construction_time_years + 1, 1)
        capex_per_year = total_capex / self.construction_time_years
        
        proforma.loc[construction_years, 'Capital Expenditure'] = -1.0 * capex_per_year
        proforma.loc[construction_years, 'Debt Contribution'] = capex_per_year * (self.leverage_pct / 100)
        proforma.loc[construction_years, 'Equity Capex'] = -1.0 * capex_per_year * (1 - self.leverage_pct / 100)

        ###### OPERATING PERIOD ######
        operating_years = proforma.index > 0
        operating_years_zero_indexed = proforma.index[operating_years] - 1

        # Calculate escalation factors for all years
        om_escalation = (1 + self.om_escalator_pct/100)**operating_years_zero_indexed
        fuel_escalation = (1 + self.fuel_escalator_pct/100)**operating_years_zero_indexed

        ### Unit Rates ###
        proforma.loc[operating_years, 'Fuel Unit Cost'] = -1.0 * self.fuel_price_dollar_per_mmbtu * fuel_escalation
        proforma.loc[operating_years, 'Solar Fixed O&M Rate'] = -1.0 * self.om_solar_fixed_dollar_per_kw * om_escalation
        proforma.loc[operating_years, 'Battery Fixed O&M Rate'] = -1.0 * self.om_bess_fixed_dollar_per_kw * om_escalation
        proforma.loc[operating_years, 'Generator Fixed O&M Rate'] = -1.0 * self.om_generator_fixed_dollar_per_kw * om_escalation
        proforma.loc[operating_years, 'Generator Variable O&M Rate'] = -1.0 * self.om_generator_variable_dollar_per_kwh * om_escalation
        proforma.loc[operating_years, 'BOS Fixed O&M Rate'] = -1.0 * self.om_bos_fixed_dollar_per_kw_load * om_escalation
        proforma.loc[operating_years, 'Soft O&M Rate'] = -1.0 * self.om_soft_pct * om_escalation

        # Calculate Fixed O&M Cost
        proforma.loc[operating_years, 'Fixed O&M Cost'] = (
            (proforma.loc[operating_years, 'Solar Fixed O&M Rate'] * self.solar_pv_capacity_mw * 1000 +
             proforma.loc[operating_years, 'Battery Fixed O&M Rate'] * self.bess_max_power_mw * 1000 +
             proforma.loc[operating_years, 'Generator Fixed O&M Rate'] * self.generator_capacity_mw * 1000 +
             proforma.loc[operating_years, 'BOS Fixed O&M Rate'] * self.datacenter_load_mw * 1000) / 1_000_000 +
            proforma.loc[operating_years, 'Soft O&M Rate'] / 100 * total_hard_capex
        )

        # Calculate Fuel Cost
        proforma.loc[operating_years, 'Fuel Cost'] = (
            proforma.loc[operating_years, 'Fuel Unit Cost'] * 
            proforma.loc[operating_years, 'Generator Fuel Input (MMBtu)']
        ) / 1_000_000
        
        # Calculate Variable O&M Cost
        proforma.loc[operating_years, 'Variable O&M Cost'] = (
            proforma.loc[operating_years, 'Generator Variable O&M Rate'] * 
            proforma.loc[operating_years, 'Generator Output (MWh)'] * 1000
        ) / 1_000_000

        # Calculate total operating costs
        proforma.loc[operating_years, 'Total Operating Costs'] = (
            proforma.loc[operating_years, 'Fuel Cost'] +
            proforma.loc[operating_years, 'Fixed O&M Cost'] +
            proforma.loc[operating_years, 'Variable O&M Cost']
        )

        ### Earnings ###
        proforma.loc[operating_years, 'LCOE'] = lcoe
        proforma.loc[operating_years, 'Revenue'] = (
            lcoe * 
            proforma.loc[operating_years, 'Load Served (MWh)']
        ) / 1_000_000

        proforma.loc[operating_years, 'EBITDA'] = (
            proforma.loc[operating_years, 'Revenue'] + 
            proforma.loc[operating_years, 'Total Operating Costs']
        )

        ### Debt, Tax, Capital ###
        for year in [y for y in years if y > 0]:
            proforma.loc[year, 'Interest Expense'] = -1.0 * proforma.loc[year, 'Debt Outstanding, Yr Start'] * interest_rate
            proforma.loc[year, 'Debt Service'] = -1.0 * fixed_debt_payment
            proforma.loc[year, 'Principal Payment'] = proforma.loc[year, 'Debt Service'] - proforma.loc[year, 'Interest Expense']
            
            if year < self.debt_term_years:
                proforma.loc[year+1, 'Debt Outstanding, Yr Start'] = (
                    proforma.loc[year, 'Debt Outstanding, Yr Start'] + 
                    proforma.loc[year, 'Principal Payment']
                )
            
            proforma.loc[year, 'Depreciation Schedule'] = self.depreciation_schedule[year-1] if year <= len(self.depreciation_schedule) else 0
            proforma.loc[year, 'Depreciation (MACRS)'] = -1.0 * (proforma.loc[year, 'Depreciation Schedule'] / 100) * amount_that_is_depreciable

            proforma.loc[year, 'Taxable Income'] = (
                proforma.loc[year, 'EBITDA'] + 
                proforma.loc[year, 'Depreciation (MACRS)'] + 
                proforma.loc[year, 'Interest Expense']
            )
            proforma.loc[year, 'Interest Expense (Tax)'] = proforma.loc[year, 'Interest Expense']

        tax_on_income = proforma['Taxable Income'] * (self.combined_tax_rate_pct / 100)
        proforma['Tax Benefit (Liability)'] = -1.0 * tax_on_income + proforma['Federal ITC'].fillna(0)

        proforma['After-Tax Net Equity Cash Flow'] = (
            proforma['EBITDA'].fillna(0) +
            proforma['Debt Service'].fillna(0) +
            proforma['Tax Benefit (Liability)'].fillna(0) +
            proforma['Equity Capex'].fillna(0)
        )

        # Calculate NPVs for financial metrics
        for col in proforma.columns:
            if col in CALCULATE_TOTALS:
                proforma.loc['NPV', col] = proforma.loc[proforma.index != 'NPV', col].sum()
            elif col in EXCLUDE_FROM_NPV:
                proforma.loc['NPV', col] = None
            elif col not in ['Operating Year']:
                values = proforma.loc[proforma.index != 'NPV', col].fillna(0)
                proforma.loc['NPV', col] = self._calculate_npv(values)

        return proforma
    
    def _calculate_npv(self, values: pd.Series) -> float:
        """Calculate NPV of a series of cash flows."""
        values = values.astype(float).fillna(0)
        years = values.index.astype(float) + self.construction_time_years
        return sum(values / (1 + self.cost_of_equity_pct/100)**years)

    def calculate_lcoe(self) -> Tuple[float, pd.DataFrame]:
        """Calculate LCOE by seeking NPV of equity cash flows = 0 
        This uses Newton's method, which converges much faster than the bisection method.
        
        Returns:
            Tuple[float, pd.DataFrame]: LCOE and proforma
            
        Raises:
            ValueError: If lifetime energy is zero or negative (system cannot serve load)
        """
        # Check for zero lifetime energy before attempting LCOE calculation
        # Calculate total from filtered simulation data directly (more efficient than full proforma)
        total_load_served = self.filtered_simulation_data['Load Served (MWh)'].sum()
        
        if total_load_served is None or pd.isna(total_load_served) or total_load_served <= 0:
            raise ValueError(f"Zero or negative lifetime energy: {total_load_served} MWh. Cannot calculate LCOE.")
        
        # Initial guess: average of bounds
        lcoe_guess = (LCOE_OPT_LOWER_BOUND + LCOE_OPT_UPPER_BOUND) / 2
        
        for iteration in range(LCOE_OPT_MAX_ITERATIONS):
            # Calculate NPV at current LCOE
            proforma = self.calculate_pro_forma(lcoe_guess)
            npv = proforma.loc['NPV', 'After-Tax Net Equity Cash Flow']
            
            # Check if we've converged
            if abs(npv) < LCOE_OPT_TOLERANCE:
                return lcoe_guess, proforma
                
            # Approximate derivative using small delta
            delta = lcoe_guess * 0.001
            npv2 = self.calculate_pro_forma(lcoe_guess + delta).loc['NPV', 'After-Tax Net Equity Cash Flow']
            derivative = (npv2 - npv) / delta
            
            # Guard against zero derivative (shouldn't happen, but be safe)
            if abs(derivative) < 1e-10:
                raise ValueError(f"Derivative too small in LCOE calculation: {derivative}")
            
            # Calculate Newton step
            lcoe_new = lcoe_guess - npv / derivative
            
            # Add bounds checking to prevent negative LCOE
            if lcoe_new <= 0:
                lcoe_guess = lcoe_guess / 2  # Back off more conservatively
            else:
                lcoe_guess = lcoe_new
                
        # If we hit max iterations, return current best estimate
        return lcoe_guess, proforma