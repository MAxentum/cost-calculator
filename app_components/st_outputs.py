"""Module for Streamlit-specific output components including charts and formatting."""

import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from typing import Dict, Optional
import polars as pl

from app_components.utils import sanitize_dataframe_for_streamlit

# Global color constants
SOLAR_COLOR = '#ffd700'  # yellow
BESS_COLOR = '#ff7f0e'   # orange
GENERATOR_COLOR = '#808080'  # gray
SYSTEM_INTEGRATION_COLOR = '#1f77b4'  # blue
SOFT_COSTS_COLOR = '#2ca02c'  # green
DATACENTER_COLOR = '#1f77b4'

# Battery constants
BATTERY_DURATION_HOURS = 4

# Global units definition
METRIC_UNITS = {
    'Operating Year': 'years',
    'Solar Output - Raw (MWh)': 'MWh',
    'Solar Output - Net (MWh)': 'MWh',
    'BESS charged (MWh)': 'MWh',
    'BESS discharged (MWh)': 'MWh',
    'Generator Output (MWh)': 'MWh',
    'Generator Fuel Input (MMBtu)': 'MMBtu',
    'Load Served (MWh)': 'MWh',
    'Fuel Unit Cost': '$/MMBtu',
    'Solar Fixed O&M Rate': '$/kW',
    'Battery Fixed O&M Rate': '$/kW',
    'Generator Fixed O&M Rate': '$/kW',
    'Generator Variable O&M Rate': '$/MWh',
    'BOS Fixed O&M Rate': '$/kW-load',
    'Soft O&M Rate': '% of hard capex',
    'LCOE': '$/MWh',
    'Revenue': '$, Millions',
    'Fuel Cost': '$, Millions',
    'Fixed O&M Cost': '$, Millions',
    'Variable O&M Cost': '$, Millions',
    'Total Operating Costs': '$, Millions',
    'EBITDA': '$, Millions',
    'Debt Outstanding, Yr Start': '$, Millions',
    'Interest Expense': '$, Millions',
    'Principal Payment': '$, Millions',
    'Debt Service': '$, Millions',
    'Depreciation Schedule': '%',
    'Depreciation (MACRS)': '$, Millions',
    'Taxable Income': '$, Millions',
    'Federal ITC': '$, Millions',
    'Tax Benefit (Liability)': '$, Millions',
    'Capital Expenditure': '$, Millions',
    'Debt Contribution': '$, Millions',
    'Equity Capex': '$, Millions',
    'After-Tax Net Equity Cash Flow': '$, Millions'
}

def display_intro_section():
    st.set_page_config(layout="wide", page_title="Solar Data Center LCOE Calculator")
    # Add custom CSS to reduce top padding
    st.markdown("""
        <style>
            .block-container {
                padding-top: 1rem;
                padding-bottom: 0rem;
            }
            #solar-datacenter-cost-calculator {
                margin-bottom: 0rem;
                padding-bottom: 0rem;
            }
            p {
                margin-bottom: 0rem;
            }
        </style>
    """, unsafe_allow_html=True)
    st.title("Solar datacenter cost calculator", anchor="solar-datacenter-cost-calculator")
    st.markdown(
        '<p style="font-size: 1em; margin-bottom: 20px;">By <a href="https://benjames.io">Ben James</a> and the <a href="https://offgridai.us">offgridai.us</a> team</p>',
        unsafe_allow_html=True
    )
    
    st.markdown(
        """
        This tool calculates the cost of electricity for a datacenter powered by solar, batteries, and gas generation.
        1. Input the generation mix, location, and financial assumptions
        2. Solar generation is fetched for the selected location, and solar/battery/generator powerflow is simulated
        3. The tool calculates the Levelised Cost of Energy (LCOE) of the system. It's designed to let you run scenarios across many designs and locations.
        4. The code powering this tool is [open-source](https://github.com/offgridai-us/cost-calculator).
        5. Parameters adapted for Axentum system.
        """
    )

def create_capex_chart(capex_subtotals: Dict[str, Dict[str, float]]) -> go.Figure:
    """Create a horizontal bar chart showing CAPEX breakdown with component details in hover."""
    bars = []
    
    # Define category display names and colors
    categories = {
        'solar': {'display': 'Solar', 'color': SOLAR_COLOR},
        'bess': {'display': 'BESS', 'color': BESS_COLOR},
        'generator': {'display': 'Generator', 'color': GENERATOR_COLOR},
        'system_integration': {'display': 'System Integration', 'color': SYSTEM_INTEGRATION_COLOR},
        'soft_costs': {'display': 'Soft Costs', 'color': SOFT_COSTS_COLOR}
    }
    
    # Calculate total CAPEX
    total_capex = sum(cat_data['total_absolute'] for cat_data in capex_subtotals.values())
    
    # Sort categories by their total values in descending order
    sorted_categories = sorted(
        [(cat, info) for cat, info in categories.items() if cat in capex_subtotals],
        key=lambda x: capex_subtotals[x[0]]['total_absolute'],
        reverse=True
    )
    
    for category, info in sorted_categories:
        category_data = capex_subtotals[category]
        value = float(category_data['total_absolute'])  # Ensure float conversion
        
        # Simple hover text with just the total and percentage
        hover_text = f"<b>{info['display']}</b><br>${value:.1f}M<br>{float(value)/float(total_capex)*100:.1f}% of Total CAPEX"

        bars.append(
            go.Bar(
                name=info['display'],
                x=[float(value)],  # Ensure float
                y=[''],
                orientation='h',
                marker_color=info['color'],
                text=f"${float(value):.1f}M",
                textposition='inside',
                textfont=dict(size=16, color='#111'),
                hoverinfo='text',
                hovertext=hover_text
            )
        )

    fig = go.Figure(data=bars)

    fig.update_layout(
        xaxis_title='CAPEX Cost ($ Millions)',
        barmode='stack',
        height=150,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.15,
            xanchor="center",
            x=0.5,
            traceorder="normal",
            font=dict(size=14)
        ),
        margin=dict(t=50, b=30, l=0, r=0),
        yaxis=dict(showticklabels=False),
        xaxis=dict(
            title_font=dict(size=14),
            tickfont=dict(size=14)
        )
    )

    return fig

def create_energy_mix_chart(energy_mix: Dict[str, float]) -> go.Figure:
    """Create a stacked horizontal bar chart showing energy mix breakdown."""
    total_energy = energy_mix['total_load_twh']
    
    fig = go.Figure(data=[
        go.Bar(
            name='Solar (direct)',
            x=[energy_mix['solar_to_load_twh']],
            y=[''],
            orientation='h',
            marker_color=SOLAR_COLOR,
            text=f"{energy_mix['solar_to_load_twh']:,.0f} TWh",
            textposition='inside',
            textfont=dict(size=16, color='#111'),
            hovertemplate="<b>Solar (direct): %{x:.1f} TWh</b><br><br>%{customdata:.1f}% of Total Energy<extra></extra>",
            customdata=[(energy_mix['solar_to_load_twh']/total_energy)*100]
        ),
        go.Bar(
            name='Solar (via BESS)',
            x=[energy_mix['bess_to_load_twh']],
            y=[''],
            orientation='h',
            marker_color=BESS_COLOR,
            text=f"{energy_mix['bess_to_load_twh']:,.0f} TWh",
            textposition='inside',
            textfont=dict(size=16, color='#111'),
            hovertemplate="<b>Solar (via BESS): %{x:.1f} TWh</b><br><br>%{customdata:.1f}% of Total Energy<extra></extra>",
            customdata=[(energy_mix['bess_to_load_twh']/total_energy)*100]
        ),
        go.Bar(
            name='Generator',
            x=[energy_mix['generator_twh']],
            y=[''],
            orientation='h',
            marker_color=GENERATOR_COLOR,
            text=f"{energy_mix['generator_twh']:,.0f} TWh",
            textposition='inside',
            textfont=dict(size=16, color='#111'),
            hovertemplate="<b>Generator: %{x:.1f} TWh</b><br><br>%{customdata:.1f}% of Total Energy<extra></extra>",
            customdata=[(energy_mix['generator_twh']/total_energy)*100]
        )
    ])

    fig.update_layout(
        title=dict(
            text='Lifetime Energy to Load (TWh)',
            font=dict(size=14),
            y=0.95  # Adjusted from default
        ),
        barmode='stack',
        height=165,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
            traceorder="normal",
            font=dict(size=14)
        ),
        margin=dict(t=70, b=30, l=0, r=0),
        yaxis=dict(showticklabels=False),
        xaxis=dict(
            title='Energy (TWh)',
            title_font=dict(size=14),
            tickfont=dict(size=14)
        )
    )
    
    return fig

def create_capacity_chart(datacenter_demand: float, solar_pv_capacity: float, 
                         bess_max_power: float, generator_capacity: float) -> go.Figure:
    """Create a bar chart showing system capacity overview."""
    fig = go.Figure(data=[
        go.Bar(name='Capacity (MW)', 
               x=['Data Center', 'Solar PV', 'BESS', 'Generator'],
               y=[datacenter_demand, solar_pv_capacity, bess_max_power, generator_capacity],
               text=[f'{int(val)} MW' for val in [datacenter_demand, solar_pv_capacity, bess_max_power, generator_capacity]],
               textposition='auto',
               marker_color=[DATACENTER_COLOR, SOLAR_COLOR, BESS_COLOR, GENERATOR_COLOR])
    ])
    fig.update_layout(
        title='System Capacity Overview',
        height=270,
        showlegend=False,
        margin=dict(t=30, b=0, l=0, r=0)
    )
    return fig

def display_daily_sample_chart(daily_sample: pl.DataFrame) -> None:
    """Display a daily sample chart showing solar generation over time."""
    daily_sample_pd = daily_sample.set_index('time_local')
    
    fig = go.Figure(data=[
        go.Scatter(
            x=daily_sample_pd.index,
            y=daily_sample_pd['scaled_solar_generation_mw'],
            mode='lines',
            name='Solar Generation (AC)',
            line=dict(color=SOLAR_COLOR, width=2),
            hovertemplate='%{y:.1f} MW<extra></extra>'
        ),
        go.Scatter(
            x=daily_sample_pd.index,
            y=daily_sample_pd['battery_discharge_mwh'] - daily_sample_pd['battery_charge_mwh'],
            mode='lines',
            name='Battery',
            line=dict(color=BESS_COLOR, width=2),
            hovertemplate='%{y:.1f} MW<extra></extra>'
        ),
        go.Scatter(
            x=daily_sample_pd.index,
            y=daily_sample_pd['generator_output_mwh'],
            mode='lines',
            name='Generator Output',
            line=dict(color=GENERATOR_COLOR, width=2),
            hovertemplate='%{y:.1f} MW<extra></extra>'
        ),
        go.Scatter(
            x=daily_sample_pd.index,
            y=daily_sample_pd['load_served_mwh'],
            mode='lines',
            name='Data Center Load',
            line=dict(color=DATACENTER_COLOR, width=2),
            hovertemplate='%{y:.1f} MW<extra></extra>'
        )
    ])
    
    fig.update_layout(
        height=360,
        margin=dict(t=30, b=50, l=0, r=0),
        xaxis_title='Hours',
        yaxis_title='Power (MW)',
        legend=dict(
            orientation="h",
            yanchor="top",
            y=1.08,  # Moved up from 1.02
            xanchor="center",
            x=0.5,
            font=dict(size=11),
            traceorder="normal"
        ),
        showlegend=True
    )
    
    st.plotly_chart(fig, use_container_width=True)

def format_proforma(proforma: pd.DataFrame) -> pd.DataFrame:
    """Format proforma with years as columns and metrics as rows."""
    # Define row groups and their metrics
    row_groups = {
        'Consumption': [
            'Solar Output - Net (MWh)',
            'BESS Net Output (MWh)',
            'Generator Output (MWh)',
            'Generator Fuel Input (MMBtu)',
            'Load Served (MWh)'
        ],
        'Rates': [
            'Fuel Unit Cost',
            'Solar Fixed O&M Rate',
            'Battery Fixed O&M Rate',
            'Generator Fixed O&M Rate',
            'Generator Variable O&M Rate',
            'BOS Fixed O&M Rate',
            'Soft O&M Rate'
        ],
        'Earnings': [
            'LCOE',
            'Revenue',
            'Fuel Cost',
            'Fixed O&M Cost',
            'Variable O&M Cost',
            'Total Operating Costs',
            'EBITDA'
        ],
        'Debt': [
            'Debt Outstanding, Yr Start',
            'Interest Expense',
            'Principal Payment',
            'Debt Service'
        ],
        'Tax': [
            'Depreciation Schedule',
            'Depreciation (MACRS)',
            'Interest Expense (Tax)',
            'Taxable Income',
            'Federal ITC',
            'Tax Benefit (Liability)'
        ],
        'Capital': [
            'Capital Expenditure',
            'Debt Contribution',
            'Equity Capex'
        ],
        'Returns': [
            'After-Tax Net Equity Cash Flow'
        ]
    }

    # Create rows for the formatted dataframe
    rows = []
    
    # Add metrics by group
    for group, metrics in row_groups.items():
        # Add group header
        rows.append({
            'Group': group,
            'Metric': '',
            'Units': '',
            'Totals/NPV': None,
            **{str(year): None for year in proforma.index if year != 'NPV'}
        })
        
        # Add metrics in the group
        for metric in metrics:
            if metric in proforma.columns:
                # Convert numpy types to Python types
                # Using try-except to handle any unexpected non-numeric values gracefully
                try:
                    npv_value = proforma.loc['NPV', metric]
                    if hasattr(npv_value, 'item'):  # Check if it's a numpy type
                        npv_value = float(npv_value.item())  # Convert to Python float
                    else:
                        npv_value = float(npv_value)
                except (ValueError, TypeError):
                    npv_value = None  # Use None for values that can't be converted
                
                year_values = {}
                for year in proforma.index:
                    if year != 'NPV':
                        try:
                            val = proforma.loc[year, metric]
                            if hasattr(val, 'item'):
                                val = float(val.item())
                            else:
                                val = float(val)
                        except (ValueError, TypeError):
                            val = None  # Use None for values that can't be converted
                        year_values[str(year)] = val

                rows.append({
                    'Group': '',
                    'Metric': metric,
                    'Units': METRIC_UNITS.get(metric, ''),
                    'Totals/NPV': npv_value,
                    **year_values
                })
    
    # Create DataFrame
    display_df = pd.DataFrame(rows)
    
    # Ensure numeric columns have proper dtype (float64 with NaN for None values)
    # This prevents Arrow serialization issues with mixed types
    numeric_columns = ['Totals/NPV'] + [str(year) for year in proforma.index if year != 'NPV']
    for col in numeric_columns:
        if col in display_df.columns:
            display_df[col] = pd.to_numeric(display_df[col], errors='coerce')
    
    return display_df

def display_proforma(proforma: Optional[pd.DataFrame]) -> None:
    """Display proforma in Streamlit with proper formatting and styling."""
    if proforma is None:
        st.error("No matching simulation data found for the selected inputs.")
        return
    
    # Sanitize DataFrame for Arrow serialization
    display_df = sanitize_dataframe_for_streamlit(proforma)
    
    # Create a style function for negative numbers
    def style_negative(val):
        if isinstance(val, (int, float)) and val < 0:
            return 'color: red; font-weight: bold;'
        return ''
    
    # Format numbers with units and handle negatives
    def format_numbers(val):
        if pd.isna(val) or val == '' or not isinstance(val, (int, float)):
            return val
        
        # Find the row containing this value
        mask = display_df.iloc[:, 3:].isin([val]).any(axis=1)  # Skip Group, Metric, Units columns
        if not mask.any():
            return val
            
        row_idx = mask.idxmax()
        unit = display_df.loc[row_idx, 'Units']
        is_negative = val < 0
        abs_val = abs(val)
        
        # Format based on unit type
        if unit in ['MWh', 'MMBtu']:
            formatted = f"{abs_val:,.0f}"
        elif unit.startswith('$'):
            formatted = f"${abs_val:,.2f}"
        elif unit.startswith('%'):
            formatted = f"{abs_val:,.2f}%"
        else:
            formatted = f"{abs_val:,.2f}"
        
        # Add brackets for negative values
        if is_negative:
            return f"({formatted})"
        return formatted
    
    # Apply styling and formatting
    styled_df = display_df.style.map(style_negative).format(format_numbers)
    
    # Add group styling
    def highlight_groups(x):
        return ['background-color: #f0f2f6' if x['Group'] != '' else '' for _ in x]
    
    # Add Totals/NPV column highlighting
    def highlight_totals_column(x):
        return ['background-color: #e6f3ff' if col == 'Totals/NPV' else '' for col in x.index]
    
    styled_df = (styled_df
                .apply(highlight_groups, axis=1)
                .apply(highlight_totals_column, axis=1))
    
    # Display with frozen columns
    st.dataframe(
        styled_df,
        column_config={
            "Group": st.column_config.Column(
                width="small",
                help="Metric group"
            ),
            "Metric": st.column_config.Column(
                width="medium",
                help="Individual metric"
            ),
            "Units": st.column_config.Column(
                width="small",
                help="Metric units"
            ),
            "Totals/NPV": st.column_config.Column(
                width="small",
                help="Sum for Consumption, NPV for financial metrics"
            )
        },
        hide_index=True,
        use_container_width=True,
        height=1200
    )

def create_subcategory_capex_charts(capex_subtotals: Dict[str, Dict[str, float]]) -> None:
    """Create individual stacked bar charts for each category's components."""
    # Define base colors and their gradients (darker to lighter)
    color_gradients = {
        'solar': ['#FFB700', '#FFC430', '#FFD147', '#FFDE70', '#FFEB99'],  # More pure yellow gradients
        'bess': ['#FF6B00', '#FF8533', '#FF9955', '#FFAD77', '#FFC299'],   # Darker orange gradients
        'generator': ['#4F4F4F', '#696969', '#808080', '#A9A9A9', '#D3D3D3'],  # Gray gradients
        'system_integration': ['#0000CD', '#4169E1', '#4682B4', '#87CEEB', '#ADD8E6'],  # Blue gradients
        'soft_costs': ['#228B22', '#2E8B57', '#3CB371', '#90EE90', '#98FB98']  # Green gradients
    }

    # Category display names
    category_names = {
        'solar': 'Solar',
        'bess': 'BESS',
        'generator': 'Generator',
        'system_integration': 'System Integration',
        'soft_costs': 'Soft Costs'
    }

    # Create a chart for each category
    for category, category_data in capex_subtotals.items():
        if 'components_absolute' not in category_data:
            continue

        components = category_data['components_absolute']
        if not components:
            continue

        # Convert component values to millions and sort
        components_millions = {k: float(v) / 1_000_000 for k, v in components.items()}
        sorted_components = sorted(components_millions.items(), key=lambda x: x[1], reverse=True)

        # Create bars for each component
        bars = []
        for i, (component_name, value) in enumerate(sorted_components):
            formatted_name = component_name.replace('_', ' ').title()
            color = color_gradients[category][min(i, len(color_gradients[category])-1)]
            
            bars.append(
                go.Bar(
                    name=formatted_name,
                    x=[value],
                    y=[''],
                    orientation='h',
                    marker_color=color,
                    text=f"${value:.1f}M",
                    textposition='inside',
                    textfont=dict(size=14, color='#111'),
                    hovertemplate=f"<b>{formatted_name}</b><br>${value:.1f}M<extra></extra>"
                )
            )

        # Create figure
        fig = go.Figure(data=bars)
        
        # Calculate total for the category
        total = sum(v for _, v in sorted_components)

        # Update layout
        fig.update_layout(
            title=dict(
                text=f"{category_names[category]} Components (Total: ${total:.1f}M)",
                font=dict(size=14),
                y=0.95
            ),
            barmode='stack',
            height=180,  # Increased height to accommodate legend
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.25,  # Adjusted from 1.35 to 1.25
                xanchor="center",
                x=0.5,
                traceorder="normal",
                font=dict(size=12)
            ),
            margin=dict(t=100, b=20, l=0, r=0),  # Increased top margin
            yaxis=dict(showticklabels=False),
            xaxis=dict(
                title='Cost ($ Millions)',
                title_font=dict(size=12),
                tickfont=dict(size=12)
            )
        )

        # Display the chart
        st.plotly_chart(fig, use_container_width=True) 