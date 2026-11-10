"""Small inference API for trusted baseline artifacts."""
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .features import SUPPORTED_FEATURES
from .training import predict

logger = logging.getLogger("uvicorn.error.meteo_mlops")


class Forecast(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    valid_time: AwareDatetime
    forecast_c: float = Field(strict=True, ge=-100, le=60)
    pressure_msl_hpa: float | None = Field(default=None, strict=True, ge=800, le=1100)


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    forecasts: list[Forecast] = Field(min_length=1, max_length=1000)


class CorrectedForecast(Forecast):
    corrected_c: float


class ModelMetadata(BaseModel):
    registry_version: str | None = None
    model_version: str
    model_name: str
    station: str
    horizon_hours: int


class PredictionResponse(ModelMetadata):
    forecasts: list[CorrectedForecast]


def create_app(model_path: str | Path | None = None) -> FastAPI:
    registry_metrics = CollectorRegistry()
    requests = Counter("meteo_requests_total", "API requests", ["route", "status"], registry=registry_metrics)
    errors = Counter("meteo_errors_total", "API errors", ["route", "status"], registry=registry_metrics)
    latency = Histogram("meteo_request_duration_seconds", "API latency", ["route"],
                        buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5), registry=registry_metrics)
    predictions = Counter("meteo_predictions_total", "Successfully corrected forecast rows", registry=registry_metrics)
    model_info = Gauge("meteo_model_info", "Loaded production model", ["version", "registry_version", "method"], registry=registry_metrics)
    loaded = Gauge("meteo_model_loaded_timestamp_seconds", "Model process load time (not training age)", registry=registry_metrics)
    trained = Gauge("meteo_model_trained_timestamp_seconds", "Training timestamp when available", registry=registry_metrics)
    quality = Gauge("meteo_holdout_quality", "Historical holdout evaluation, not live verification",
                    ["method", "metric"], registry=registry_metrics)

    verification = Gauge("meteo_verification_quality", "Explicit matched observation verification",
                         ["method", "metric"], registry=registry_metrics)
    verification_available = Gauge("meteo_verification_available", "Checksum-matched verification available", registry=registry_metrics)
    verification_end = Gauge("meteo_verification_end_timestamp_seconds", "Latest matched observation time", registry=registry_metrics)
    verification_samples = Gauge("meteo_verification_samples", "Matched verification rows", registry=registry_metrics)

    @asynccontextmanager
    async def lifespan(app):
        path = Path(model_path or os.environ.get("MODEL_PATH", "artifacts/baseline/model.joblib"))
        registry_path = path.with_name("registry.json")
        registry = json.loads(registry_path.read_text()) if registry_path.exists() else None
        if registry and registry["sha256"] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("Registry bundle checksum mismatch")
        # Joblib deserialization executes code: only deploy trusted artifacts.
        artifact = joblib.load(path)
        if (artifact.get("schema_version") != 2 or artifact.get("features") not in SUPPORTED_FEATURES
                or artifact.get("station") != "NZWN" or artifact.get("horizon_hours") != 24
                or artifact.get("forecast_model") != "gfs_global"):
            raise ValueError("Incompatible model artifact contract")
        metadata = ModelMetadata(registry_version=registry["version"] if registry else None, model_version=hashlib.sha256(path.read_bytes()).hexdigest(),
                                 model_name=artifact["model_name"], station=artifact["station"],
                                 horizon_hours=artifact["horizon_hours"])
        probe = predict(artifact, pd.DataFrame({"valid_time": ["2026-01-01T12:00Z"], "forecast_c": [18.0], "pressure_msl_hpa": [1013.25]}))
        if np.shape(probe) != (1,) or not np.isfinite(probe).all():
            raise ValueError("Model startup prediction failed")
        app.state.artifact, app.state.metadata = artifact, metadata
        model_info.labels(metadata.model_version, metadata.registry_version or "unregistered", metadata.model_name).set(1)
        loaded.set(time.time())
        trained.set(artifact.get("trained_timestamp_seconds", float("nan")))
        for method, scores in artifact.get("holdout_quality", {}).items():
            for metric in ("rmse_c", "bias_c", "rmse_skill", "rolling_bias_c"):
                if scores.get(metric) is not None:
                    quality.labels(method, metric).set(scores[metric])
        logger.info("model_loaded version=%s method=%s", metadata.model_version, metadata.model_name)
        try:
            yield
        finally:
            app.state.artifact = None

    app = FastAPI(title="Weather recalibration", version="0.1.0", lifespan=lifespan)
    app.state.artifact = None

    @app.middleware("http")
    async def observe(request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)
        start = time.monotonic()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            # Fixed vocabulary prevents arbitrary URLs, query values or user IDs becoming labels.
            route = request.url.path if request.url.path in ("/health", "/ready", "/predict") else "other"
            requests.labels(route, status).inc()
            if int(status) >= 400:
                errors.labels(route, status).inc()
            latency.labels(route).observe(time.monotonic() - start)

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint():
        verification.clear()
        verification_available.set(0)
        verification_end.set(float("nan"))
        verification_samples.set(0)
        quality_path = os.environ.get("QUALITY_PATH")
        if quality_path and app.state.artifact is not None:
            try:
                payload = json.loads(Path(quality_path).read_text())
                if payload["sha256"] != app.state.metadata.model_version:
                    raise ValueError("Verification belongs to another model")
                cutoff = pd.to_datetime(app.state.artifact["config"]["splits"]["validation"][1], utc=True).timestamp()
                if payload["start"] < cutoff + 24 * 3600 + 600 or payload["end"] < payload["start"] or payload["samples"] < 168:
                    raise ValueError("Invalid verification window")
                validated = []
                for method in ("raw", "corrected"):
                    for metric in ("rmse_c", "bias_c", "rmse_skill", "rolling_bias_c"):
                        value = payload["quality"][method][metric]
                        if value is not None:
                            if not math.isfinite(value):
                                raise ValueError("Nonfinite verification")
                            validated.append((method, metric, value))
                for method, metric, value in validated:
                    verification.labels(method, metric).set(value)
                verification_end.set(payload["end"])
                verification_samples.set(payload["samples"])
                verification_available.set(1)
            except (OSError, ValueError, KeyError, TypeError):
                # Never advertise stale/mismatched scores as current model quality.
                pass
        return Response(generate_latest(registry_metrics), headers={"Content-Type": CONTENT_TYPE_LATEST})

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready", response_model=ModelMetadata)
    def ready():
        if app.state.artifact is None:
            raise HTTPException(503, "Model unavailable")
        return app.state.metadata

    @app.post("/predict", response_model=PredictionResponse)
    def inference(request: PredictionRequest):
        metadata = ready()
        if "pressure_msl_hpa" in app.state.artifact["features"] and any(
                item.pressure_msl_hpa is None for item in request.forecasts):
            raise HTTPException(status_code=422, detail="This model requires pressure_msl_hpa for every forecast")
        start = time.monotonic()
        try:
            frame = pd.DataFrame([item.model_dump() for item in request.forecasts])
            corrected = predict(app.state.artifact, frame)
            if np.shape(corrected) != (len(frame),) or not np.isfinite(corrected).all():
                raise ValueError("Invalid model output")
            response = PredictionResponse(**metadata.model_dump(), forecasts=[
                CorrectedForecast(**item.model_dump(), corrected_c=float(value))
                for item, value in zip(request.forecasts, corrected)
            ])
        except Exception:
            logger.exception("inference_failed version=%s", metadata.model_version)
            raise HTTPException(500, "Inference failed") from None
        predictions.inc(len(frame))
        logger.info("prediction version=%s count=%d duration_ms=%.2f", metadata.model_version,
                    len(frame), (time.monotonic() - start) * 1000)
        return response

    return app


app = create_app()
