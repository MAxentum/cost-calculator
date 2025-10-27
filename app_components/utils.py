"""Utility functions for Streamlit app components."""

import pandas as pd
import numpy as np
from typing import Any, Dict


def validate_case_inputs(case: Dict[str, Any]) -> str:
    """Validate inputs for a single case.
    
    Args:
        case: Dictionary with datacenter configuration
        
    Returns:
        Empty string if valid, error message if invalid
    """
    if case.get("datacenter_load_mw", 0) <= 0:
        return "invalid: datacenter_load_mw <= 0"
    if case.get("solar_pv_capacity_mw", 0) < 0:
        return "invalid: solar_pv_capacity_mw < 0"
    if case.get("bess_max_power_mw", 0) < 0:
        return "invalid: bess_max_power_mw < 0"
    if case.get("generator_capacity_mw", 0) < 0:
        return "invalid: generator_capacity_mw < 0"
    return ""


def sanitize_dataframe_for_streamlit(df: pd.DataFrame) -> pd.DataFrame:
    """Sanitize a DataFrame for safe display in Streamlit to avoid Arrow serialization errors.
    
    This function:
    - Coerces numeric-like columns to numeric types using pd.to_numeric with errors="coerce"
    - Decodes bytes columns to strings
    - Converts mixed-type columns to strings
    - Ensures compatibility with Arrow serialization used by st.dataframe and st.data_editor
    
    Args:
        df: Input DataFrame to sanitize
        
    Returns:
        Sanitized DataFrame safe for Streamlit display
    """
    if df is None or df.empty:
        return df
    
    # Create a copy to avoid modifying the original
    df_clean = df.copy()
    
    for col in df_clean.columns:
        try:
            # Skip if already a clean numeric type (no conversion needed)
            if pd.api.types.is_numeric_dtype(df_clean[col]):
                continue
            
            # Try to decode bytes to string
            if df_clean[col].dtype == object:
                # Check if column is empty
                if len(df_clean[col]) == 0:
                    continue
                
                # Check if any values are bytes
                sample = df_clean[col].dropna().head(10)
                if len(sample) > 0 and any(isinstance(val, bytes) for val in sample):
                    df_clean[col] = df_clean[col].apply(
                        lambda x: x.decode('utf-8') if isinstance(x, bytes) else x
                    )
                
                # Try to convert to numeric if it looks numeric
                # This handles cases where numbers are stored as strings
                numeric_converted = pd.to_numeric(df_clean[col], errors='coerce')
                # If at least some values converted successfully, use the numeric version
                if numeric_converted.notna().sum() > 0:
                    # Only convert if most values are numeric (>50%)
                    if len(df_clean[col]) > 0 and numeric_converted.notna().sum() / len(df_clean[col]) > 0.5:
                        df_clean[col] = numeric_converted
                    else:
                        # Mixed types - convert all to string for consistency
                        df_clean[col] = df_clean[col].astype(str)
                else:
                    # Ensure string type for non-numeric object columns
                    df_clean[col] = df_clean[col].astype(str)
            
            # Handle datetime types
            elif pd.api.types.is_datetime64_any_dtype(df_clean[col]):
                # Convert to string to avoid Arrow datetime issues
                df_clean[col] = df_clean[col].astype(str)
                
        except Exception:
            # If any conversion fails, fall back to string
            try:
                df_clean[col] = df_clean[col].astype(str)
            except Exception:
                # Last resort: keep as is
                pass
    
    return df_clean
