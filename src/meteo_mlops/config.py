"""Load the explicit, versioned experiment configuration."""
import json
from importlib.resources import files
import pandas as pd


def load_config(path=None, training_date=None):
    config = json.loads(path.read_text() if path else files("meteo_mlops").joinpath("config.json").read_text())
    expected = {"ridge_alphas", "seed", "xgboost_candidates", "splits", "minimum_split_hours"}
    optional = {"features", "rolling_window"}
    if set(config) - optional != expected:
        raise ValueError(f"Config requires {sorted(expected)} with optional {sorted(optional)}")
    if not config["ridge_alphas"] or any(a <= 0 for a in config["ridge_alphas"]):
        raise ValueError("Ridge alphas must be positive")
    if not config["xgboost_candidates"]:
        raise ValueError("At least one XGBoost candidate is required")
    allowed = {"n_estimators", "max_depth", "learning_rate", "min_child_weight"}
    for params in config["xgboost_candidates"]:
        if set(params) != allowed or any(v <= 0 for v in params.values()):
            raise ValueError(f"XGBoost parameters must be positive and contain {sorted(allowed)}")
    if not isinstance(config["seed"], int) or config["minimum_split_hours"] < 1:
        raise ValueError("Seed must be an integer and minimum split size positive")
    if set(config["splits"]) != {"train", "validation", "test"}:
        raise ValueError("Exactly train, validation and test splits are required")
    from .features import SUPPORTED_FEATURES
    if "features" in config and config["features"] not in SUPPORTED_FEATURES:
        raise ValueError("Unsupported feature contract")
    rolling = config.get("rolling_window", False)
    if not isinstance(rolling, bool):
        raise ValueError("rolling_window must be a boolean")
    previous_end = None
    for name in ("train", "validation", "test"):
        bounds = config["splits"][name]
        if len(bounds) != 2:
            raise ValueError("Each split must have start and exclusive end")
        start, end = [pd.to_datetime(t, utc=True) for t in bounds]
        if start >= end or (previous_end is not None and start - previous_end < pd.Timedelta(hours=24, minutes=10)):
            raise ValueError("Splits must be ordered with at least a 24h10m embargo")
        previous_end = end
    if rolling:
        end = pd.Timestamp(training_date) if training_date else pd.Timestamp.now(tz="UTC")
        end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
        end = end.normalize()
        offset = end - pd.to_datetime(config["splits"]["test"][1], utc=True)
        config["splits"] = {name: [(pd.to_datetime(t, utc=True) + offset).isoformat().replace("+00:00", "Z")
                                   for t in bounds] for name, bounds in config["splits"].items()}
    return config
