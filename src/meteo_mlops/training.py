"""Deterministic residual models selected on validation, evaluated on holdout."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

from .config import load_config
from .data import prepare
from .features import FEATURES, features
from .evaluation import evaluate, metrics, write_table


def split(frame, config=None):
    config = config or load_config()
    parts = []
    for name in ("train", "validation", "test"):
        start, end = [pd.to_datetime(t, utc=True) for t in config["splits"][name]]
        part = frame[(frame.valid_time >= start) & (frame.valid_time < end)]
        if len(part) < config["minimum_split_hours"]:
            raise ValueError(f"Each temporal split requires at least {config['minimum_split_hours']} matched hours")
        parts.append(part)
    return tuple(parts)


def predict(artifact, frame):
    return frame.forecast_c.to_numpy() + artifact["model"].predict(features(frame, artifact["features"]))


def _train(root, output, config=None):
    config = config or load_config()
    names = config.get("features", FEATURES)
    frame, quality = prepare(root, pressure=True) if "pressure_msl_hpa" in names else prepare(root)
    parts = dict(zip(("train", "validation", "test"), split(frame, config)))
    training, validation = parts["train"], parts["validation"]
    x_train, x_validation = features(training, names), features(validation, names)
    residual = training.observed_c - training.forecast_c
    candidates = {"ridge": [], "xgboost": []}
    for alpha in config["ridge_alphas"]:
        model = make_pipeline(StandardScaler(), Ridge(alpha=alpha))
        model.fit(x_train, residual)
        score = metrics(validation.observed_c, validation.forecast_c + model.predict(x_validation))["rmse_c"]
        candidates["ridge"].append((score, {"alpha": alpha}, model))
    for params in config["xgboost_candidates"]:
        model = XGBRegressor(**params, objective="reg:squarederror", tree_method="hist",
                             n_jobs=1, random_state=config["seed"], subsample=1.0, colsample_bytree=1.0)
        model.fit(x_train, residual)
        score = metrics(validation.observed_c, validation.forecast_c + model.predict(x_validation))["rmse_c"]
        candidates["xgboost"].append((score, params, model))
    winners = {name: min(items, key=lambda item: item[0]) for name, items in candidates.items()}
    correction = float(residual.mean())
    # Mean correction is eligible too: do not prefer complexity over validation evidence.
    validation_scores = {name: item[0] for name, item in winners.items()}
    validation_scores["mean_bias_correction"] = metrics(validation.observed_c, validation.forecast_c + correction)["rmse_c"]
    validation_scores["raw"] = metrics(validation.observed_c, validation.forecast_c)["rmse_c"]
    selected = min(validation_scores, key=validation_scores.get)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob("*")) if p.suffix in (".json", ".csv")}
    metadata = {"schema_version": 2, "station": "NZWN", "horizon_hours": 24, "forecast_model": "gfs_global",
                "features": names, "config": config, "selected_model": selected, "mean_residual_c": correction,
                "data_sha256": hashes, "python": platform.python_version(),
                "packages": {name: version(name) for name in ("meteo-mlops", "numpy", "pandas", "scikit-learn", "xgboost", "joblib")},
                "uv_lock_sha256": hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest() if Path("uv.lock").exists() else None,
                "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob("*.py"))},
                "models": {name: {"parameters": item[1], "validation_rmse_c": item[0]} for name, item in winners.items()}}
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for name, (_, params, model) in winners.items():
        artifact = {**metadata, "model_name": name, "parameters": params, "model": model}
        joblib.dump(artifact, output / f"{name}.joblib")
        restored = joblib.load(output / f"{name}.joblib")
        np.testing.assert_array_equal(predict(artifact, parts["test"]), predict(restored, parts["test"]))
        artifacts[name] = restored
    if selected in artifacts:
        selected_artifact = artifacts[selected]
    else:
        constant = correction if selected == "mean_bias_correction" else 0.0
        selected_model = DummyRegressor(strategy="constant", constant=constant).fit(x_train, residual)
        selected_artifact = {**metadata, "model_name": selected, "model": selected_model}
    joblib.dump(selected_artifact, output / "model.joblib")
    restored_selected = joblib.load(output / "model.joblib")
    np.testing.assert_array_equal(predict(selected_artifact, parts["test"]), predict(restored_selected, parts["test"]))
    predictions = {}
    for name, part in parts.items():
        predictions[name] = {"raw": part.forecast_c.to_numpy(), "mean_bias_correction": part.forecast_c.to_numpy() + correction,
                             **{method: predict(artifact, part) for method, artifact in artifacts.items()}}
    report = {"quality": quality, "horizon_hours": 24, "selected_model": selected, "data_sha256": hashes,
              "validation_candidates": {name: [{"parameters": params, "rmse_c": score} for score, params, _ in items]
                                        for name, items in candidates.items()},
              "splits": evaluate(parts, predictions, selected)}
    selected_artifact["trained_timestamp_seconds"] = time.time()
    holdout = report["splits"]["test"]
    selected_artifact["holdout_quality"] = {
        method: {**holdout[method], "rmse_skill": holdout["rmse_skill"][method]}
        for method in dict.fromkeys(("raw", selected))
    }
    recent = parts["test"].tail(168)
    for method in dict.fromkeys(("raw", selected)):
        values = recent.forecast_c if method == "raw" else predict(selected_artifact, recent)
        selected_artifact["holdout_quality"][method]["rolling_bias_c"] = metrics(recent.observed_c, values)["bias_c"]
    joblib.dump(selected_artifact, output / "model.joblib")
    test_predictions = parts["test"].copy()
    for method, values in predictions["test"].items():
        test_predictions[f"{method}_c"] = values
    test_predictions.to_csv(output / "test-predictions.csv", index=False)
    for filename, payload in (("metrics.json", report), ("metadata.json", metadata)):
        (output / filename).write_text(json.dumps(payload, indent=2) + "\n")
    write_table(report, output / "evaluation.md")
    print(json.dumps(report, indent=2))
    return report


def train(root, output, config=None):
    from .tracking import configure, log_result
    import mlflow
    configure()
    with mlflow.start_run(run_name="temperature-recalibration"):
        report = _train(root, output, config)
        log_result(output, report)
        if (root / "manifest.json").exists():
            mlflow.log_artifact(str(root / "manifest.json"), "data")
        return report
