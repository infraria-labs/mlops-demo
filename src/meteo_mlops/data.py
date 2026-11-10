"""Forecast/observation retrieval, alignment and quality checks."""
import hashlib
import io
import json
from urllib.parse import urlencode
from urllib.request import urlopen
import numpy as np
import pandas as pd

PRESSURE_VARIABLE = "pressure_msl_previous_day1"

FORECAST_VARIABLE = "temperature_2m_previous_day1"

def download(url, path):
    if not path.exists():
        with urlopen(url, timeout=180) as response:
            payload = response.read()
        path.write_bytes(payload)
    return {"url": url, "file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def source_years(config):
    if config is None:
        return (2024, 2025)
    start = pd.to_datetime(config["splits"]["train"][0], utc=True).tz_localize(None)
    end = pd.to_datetime(config["splits"]["test"][1], utc=True).tz_localize(None) - pd.Timedelta(nanoseconds=1)
    return range(start.year, end.year + 1)


def fetch(root, config=None):
    root.mkdir(parents=True, exist_ok=True)
    sources = []
    for year in source_years(config):
        start = max(pd.Timestamp(f"{year}-01-01"), pd.to_datetime(config["splits"]["train"][0], utc=True).tz_localize(None)) if config else pd.Timestamp(f"{year}-01-01")
        end = min(pd.Timestamp(f"{year+1}-01-01"), pd.to_datetime(config["splits"]["test"][1], utc=True).tz_localize(None)) if config else pd.Timestamp(f"{year+1}-01-01")
        params = dict(latitude=-41.3272, longitude=174.8053, models="gfs_global",
                      hourly=",".join([FORECAST_VARIABLE, PRESSURE_VARIABLE]) if config and "pressure_msl_hpa" in config.get("features", []) else FORECAST_VARIABLE, start_date=start.date().isoformat(),
                      end_date=(end - pd.Timedelta(days=1)).date().isoformat(), timezone="GMT", temperature_unit="celsius")
        sources.append(download("https://previous-runs-api.open-meteo.com/v1/forecast?" + urlencode(params), root / f"forecast-{year}.json"))
        params = dict(station="NZWN", data="tmpc", year1=start.year, month1=start.month, day1=start.day,
                      year2=end.year, month2=end.month, day2=end.day, tz="Etc/UTC", format="onlycomma",
                      latlon="no", elev="no", missing="M", trace="T", direct="no", report_type=3)
        sources.append(download("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?" + urlencode(params), root / f"observations-{year}.csv"))
    (root / "manifest.json").write_text(json.dumps(sources, indent=2) + "\n")


def prepare(root, pressure=False):
    forecasts, observations = [], []
    for file in sorted(root.glob("forecast-*.json")):
        year = int(file.stem.split("-")[-1])
        payload = json.loads((root / f"forecast-{year}.json").read_text())
        if payload.get("hourly_units", {}).get(FORECAST_VARIABLE) != "°C":
            raise ValueError("Forecast must be in degrees Celsius")
        hourly = payload["hourly"]
        if pressure and payload.get("hourly_units", {}).get(PRESSURE_VARIABLE) != "hPa":
            raise ValueError("Sea level pressure must be in hPa; fetch a fresh pressure cache")
        forecasts.append(pd.DataFrame({"valid_time": pd.to_datetime(hourly["time"], utc=True),
                                       "forecast_c": hourly[FORECAST_VARIABLE],
                                       **({"pressure_msl_hpa": hourly[PRESSURE_VARIABLE]} if pressure else {})}))
        obs = pd.read_csv(io.StringIO((root / f"observations-{year}.csv").read_text()), comment="#")
        obs["valid_time"] = pd.to_datetime(obs["valid"], utc=True)
        obs["observed_c"] = pd.to_numeric(obs["tmpc"], errors="coerce")
        obs = obs[(obs.station == "NZWN") & obs.observed_c.between(-50, 50)].copy()
        # Routine METARs nearest the hour (within 10 min); never average across hours.
        obs["hour"] = obs.valid_time.dt.round("h")
        obs["distance"] = (obs.valid_time - obs.hour).abs()
        obs = obs[obs.distance <= pd.Timedelta(minutes=10)].sort_values(["distance", "valid_time"])
        obs = obs.drop_duplicates("hour").rename(columns={"valid_time": "observation_time", "hour": "valid_time"})
        observations.append(obs[["valid_time", "observation_time", "observed_c"]])
    forecast = pd.concat(forecasts).sort_values("valid_time")
    if forecast.valid_time.duplicated().any():
        raise ValueError("Duplicate forecast valid times")
    observations = pd.concat(observations)
    # Reports near midnight can round across yearly response boundaries.
    observations["distance"] = (observations.observation_time - observations.valid_time).abs()
    observations = observations.sort_values(["distance", "observation_time"]).drop_duplicates("valid_time")
    matched = forecast.merge(observations.drop(columns="distance"), on="valid_time", validate="one_to_one")
    matched = matched.dropna().sort_values("valid_time").reset_index(drop=True)
    if not np.isfinite(matched[["forecast_c", "observed_c"] + (["pressure_msl_hpa"] if pressure else [])].to_numpy()).all():
        raise ValueError("Nonfinite data")
    quality = {"forecast_hours": len(forecast), "matched_hours": len(matched),
               "coverage": len(matched) / len(forecast), "max_observation_offset_minutes": float((matched.observation_time - matched.valid_time).abs().dt.total_seconds().max() / 60)}
    if quality["coverage"] < 0.8:
        raise ValueError(f"Insufficient station coverage: {quality}")
    return matched, quality
