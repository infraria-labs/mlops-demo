import hashlib

import joblib
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sklearn.dummy import DummyRegressor

from meteo_mlops.api import create_app
from meteo_mlops.features import FEATURES
from meteo_mlops.training import predict


@pytest.fixture
def model_path(tmp_path):
    model = DummyRegressor(strategy="constant", constant=2.5).fit(np.zeros((2, 5)), [0, 0])
    artifact = dict(schema_version=2, features=FEATURES, station="NZWN", horizon_hours=24,
                    forecast_model="gfs_global", model_name="mean_bias_correction", model=model)
    path = tmp_path / "model.joblib"
    joblib.dump(artifact, path)
    return path


def test_prediction_matches_artifact_and_loads_once(model_path):
    inputs = [{"valid_time": "2026-01-01T12:00:00Z", "forecast_c": 18.0},
              {"valid_time": "2026-01-02T00:00:00+12:00", "forecast_c": 10.0}]
    expected = predict(joblib.load(model_path), pd.DataFrame(inputs)).tolist()
    version = hashlib.sha256(model_path.read_bytes()).hexdigest()
    with TestClient(create_app(model_path)) as client:
        model_path.unlink()  # Loaded at startup, not per request.
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json()["model_version"] == version
        response = client.post("/predict", json={"forecasts": inputs})
        assert response.status_code == 200
        assert response.json()["model_version"] == version
        assert [x["corrected_c"] for x in response.json()["forecasts"]] == expected


@pytest.mark.parametrize("payload", [
    {}, {"forecasts": []}, {"forecasts": [{}]},
    {"forecasts": [{"valid_time": "2026-01-01", "forecast_c": 18}]},
    {"forecasts": [{"valid_time": "garbage", "forecast_c": 18}]},
    *[{"forecasts": [{"valid_time": "2026-01-01T12:00Z", "forecast_c": value}]}
      for value in ["18", True, None, -101, 61, "NaN"]],
    {"forecasts": [{"valid_time": "2026-01-01T12:00Z", "forecast_c": 18, "observed_c": 20}]},
    {"forecasts": [{"valid_time": "2026-01-01T12:00Z", "forecast_c": 18}], "station": "OTHER"},
    {"forecasts": [{"valid_time": "2026-01-01T12:00Z", "forecast_c": 18}] * 1001},
])
def test_invalid_requests(model_path, payload):
    with TestClient(create_app(model_path)) as client:
        assert client.post("/predict", json=payload).status_code == 422


def test_not_ready():
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    assert client.post("/predict", json={"forecasts": [{"valid_time": "2026-01-01T12:00Z", "forecast_c": 18}]}).status_code == 503


def test_missing_model_fails_startup(tmp_path):
    with pytest.raises(FileNotFoundError), TestClient(create_app(tmp_path / "missing")):
        pass


def test_incompatible_model_fails_startup(model_path):
    artifact = joblib.load(model_path)
    artifact["features"] = []
    joblib.dump(artifact, model_path)
    with pytest.raises(ValueError, match="contract"), TestClient(create_app(model_path)):
        pass


@pytest.mark.parametrize("failure", [RuntimeError("private details"), np.array([np.nan]), np.array([1, 2])])
def test_inference_failure_is_clean(model_path, monkeypatch, failure):
    app = create_app(model_path)
    with TestClient(app) as client:
        def broken(_):
            if isinstance(failure, Exception):
                raise failure
            return failure
        monkeypatch.setattr(app.state.artifact["model"], "predict", broken)
        response = client.post("/predict", json={"forecasts": [{"valid_time": "2026-01-01T12:00Z", "forecast_c": 18}]})
        assert response.status_code == 500
        assert response.json() == {"detail": "Inference failed"}


def test_pressure_model_inputs_and_prediction_parity(model_path):
    from sklearn.linear_model import LinearRegression
    from meteo_mlops.features import PRESSURE_FEATURES, features
    inputs = [{'valid_time': '2026-01-01T12:00Z', 'forecast_c': 18.0, 'pressure_msl_hpa': p}
              for p in (1000.0, 1020.0)]
    frame = pd.DataFrame(inputs)
    artifact = joblib.load(model_path)
    artifact['features'] = PRESSURE_FEATURES
    artifact['model'] = LinearRegression().fit(features(frame, PRESSURE_FEATURES), [-1.0, 1.0])
    joblib.dump(artifact, model_path)
    with TestClient(create_app(model_path)) as client:
        response = client.post('/predict', json={'forecasts': inputs})
        assert response.status_code == 200
        assert [row['corrected_c'] for row in response.json()['forecasts']] == pytest.approx([17, 19])
        for pressure in (None, '1013', 0, float('inf')):
            row = {**inputs[0], 'pressure_msl_hpa': pressure}
            if pressure == float('inf'):
                row['pressure_msl_hpa'] = 'Infinity'
            assert client.post('/predict', json={'forecasts': [row]}).status_code == 422
        row = {k: v for k, v in inputs[0].items() if k != 'pressure_msl_hpa'}
        assert client.post('/predict', json={'forecasts': [row]}).status_code == 422
