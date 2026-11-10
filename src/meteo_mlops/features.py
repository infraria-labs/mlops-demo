"""Prediction-time inputs only; no station observations."""
import numpy as np
import pandas as pd

FEATURES = ["forecast_c", "hour_sin", "hour_cos", "year_sin", "year_cos"]

PRESSURE_FEATURES = FEATURES + ["pressure_msl_hpa"]
SUPPORTED_FEATURES = (FEATURES, PRESSURE_FEATURES)

def features(frame, names=None):
    time = pd.to_datetime(frame["valid_time"], utc=True)
    hour = time.dt.hour + time.dt.minute / 60
    year = (time.dt.dayofyear - 1 + hour / 24) / np.where(time.dt.is_leap_year, 366, 365)
    result = pd.DataFrame({"forecast_c": frame.forecast_c, "hour_sin": np.sin(2*np.pi*hour/24),
                         "hour_cos": np.cos(2*np.pi*hour/24), "year_sin": np.sin(2*np.pi*year),
                         "year_cos": np.cos(2*np.pi*year)}, index=frame.index)
    names = FEATURES if names is None else names
    if names not in SUPPORTED_FEATURES:
        raise ValueError("Unsupported feature contract")
    if "pressure_msl_hpa" in names:
        result["pressure_msl_hpa"] = frame["pressure_msl_hpa"]
    values = result[names]
    if not np.isfinite(values.to_numpy()).all():
        raise ValueError("Nonfinite prediction features")
    return values
