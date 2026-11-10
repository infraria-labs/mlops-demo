import numpy as np
import pandas as pd
import pytest
from meteo_mlops.features import features
from meteo_mlops.evaluation import metrics
from meteo_mlops.training import split


def test_metrics_sign_and_scale():
    assert metrics([1, 3], [3, 3]) == {"rmse_c": np.sqrt(2), "mae_c": 1, "bias_c": 1}


def test_temporal_split_has_forecast_and_matching_embargo():
    frame = pd.DataFrame({"valid_time": pd.date_range("2024-01-01", "2025-12-31T23:00", freq="h", tz="UTC")})
    train, validation, test = split(frame)
    assert train.valid_time.max() + pd.Timedelta(hours=24, minutes=10) < validation.valid_time.min()
    assert validation.valid_time.max() + pd.Timedelta(hours=24, minutes=10) < test.valid_time.min()
    assert set(train.index).isdisjoint(test.index)


def test_features_do_not_use_observation():
    frame = pd.DataFrame({"valid_time": pd.to_datetime(["2025-01-01T00:00Z"]), "forecast_c": [12], "observed_c": [15]})
    before = features(frame)
    frame["observed_c"] = -999
    pd.testing.assert_frame_equal(before, features(frame))


def test_empty_split_rejected():
    with pytest.raises(ValueError, match="1000"):
        split(pd.DataFrame({"valid_time": pd.to_datetime([], utc=True)}))


@pytest.mark.parametrize("pressure", [False, True])
def test_training_reload_and_repeat(tmp_path, monkeypatch, capsys, pressure):
    from meteo_mlops import training as baseline
    import joblib
    frame = pd.DataFrame({"valid_time": pd.date_range("2024-01-01", "2025-12-31T23:00", freq="h", tz="UTC")})
    frame["forecast_c"] = 12 + 4 * np.sin(np.arange(len(frame)) / 1000)
    frame["observed_c"] = frame.forecast_c + 1 + np.sin(frame.valid_time.dt.hour * 2 * np.pi / 24)
    from meteo_mlops.config import load_config
    from meteo_mlops.features import PRESSURE_FEATURES
    config = load_config()
    if pressure:
        frame["pressure_msl_hpa"] = 1013 + np.sin(np.arange(len(frame)) / 20)
        config = load_config(baseline.Path("experiments/pressure-rolling.json"), "2026-01-01")
        config["features"] = PRESSURE_FEATURES
    monkeypatch.setattr(baseline, "prepare", lambda root, **kwargs: (frame, {"coverage": 1.0}))
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "input.csv").write_text("provenance fixture")
    first, second = tmp_path / "first", tmp_path / "second"
    baseline.train(raw, first, config)
    baseline.train(raw, second, config)
    assert (first / "metrics.json").read_bytes() == (second / "metrics.json").read_bytes()
    assert (first / "test-predictions.csv").read_bytes() == (second / "test-predictions.csv").read_bytes()
    restored = joblib.load(first / "model.joblib")
    assert restored["horizon_hours"] == 24
    report = baseline.json.loads((first / "metrics.json").read_text())
    assert "input.csv" in report["data_sha256"]
    assert report["splits"]["test"]["ridge"]["rmse_c"] < 0.1
    assert (first / "xgboost.joblib").exists()
    assert (first / "metadata.json").exists()
    import mlflow
    from meteo_mlops.tracking import MODEL
    tracking = baseline.json.loads((first / "tracking.json").read_text())
    client = mlflow.MlflowClient()
    run = client.get_run(tracking["run_id"])
    assert run.info.status == "FINISHED"
    assert run.data.tags["data_version"]
    assert "git_sha" in run.data.tags
    assert "features" in run.data.params
    assert "test.ridge.mae_c" in run.data.metrics
    for uri in (f"runs:/{tracking['run_id']}/bundle",
                f"models:/{MODEL}/{tracking['model_version']}"):
        restored_path = mlflow.artifacts.download_artifacts(artifact_uri=uri)
        model = joblib.load(baseline.Path(restored_path) / "model.joblib")
        np.testing.assert_array_equal(baseline.predict(restored, frame), baseline.predict(model, frame))
    client.set_registered_model_alias(MODEL, "champion", tracking["model_version"])
    assert client.get_model_version_by_alias(MODEL, "champion").version == tracking["model_version"]
    assert any(a.path.endswith("holdout-rmse.png") for a in client.list_artifacts(tracking["run_id"], "evaluation"))
    assert "xgboost" in (first / "evaluation.md").read_text()
    capsys.readouterr()


def test_config_rejects_leaking_boundaries(tmp_path):
    import json
    from meteo_mlops.config import load_config
    config = load_config()
    config["splits"]["validation"][0] = "2024-12-30"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="embargo"):
        load_config(path)


def test_preprocessing_selects_nearest_report_and_filters_invalid(tmp_path):
    import json
    from meteo_mlops.data import FORECAST_VARIABLE, prepare
    for year in (2024, 2025):
        (tmp_path / f"forecast-{year}.json").write_text(json.dumps({
            "hourly_units": {FORECAST_VARIABLE: "°C"},
            "hourly": {"time": [f"{year}-02-01T00:00", f"{year}-02-01T01:00"], FORECAST_VARIABLE: [10, 11]}}))
        (tmp_path / f"observations-{year}.csv").write_text(
            "station,valid,tmpc\n"
            f"NZWN,{year}-02-01 00:05,12\nNZWN,{year}-02-01 00:00,13\n"
            f"NZWN,{year}-02-01 01:00,M\nNZWN,{year}-02-01 01:05,14\n"
            f"NZWN,{year}-02-01 01:01,999\nOTHER,{year}-02-01 01:00,9\n")
    matched, quality = prepare(tmp_path)
    assert matched.observed_c.tolist() == [13, 14, 13, 14]
    assert quality["coverage"] == 1
    assert quality["max_observation_offset_minutes"] == 5


def test_test_targets_cannot_change_model_selection(tmp_path, monkeypatch, capsys):
    from meteo_mlops import training
    from meteo_mlops.config import load_config
    import joblib
    frame = pd.DataFrame({"valid_time": pd.date_range("2024-01-01", "2025-12-31T23:00", freq="h", tz="UTC")})
    frame["forecast_c"] = 10 + np.sin(np.arange(len(frame)) / 100)
    frame["observed_c"] = frame.forecast_c + np.cos(frame.valid_time.dt.hour * 2 * np.pi / 24)
    monkeypatch.setattr(training, "prepare", lambda root: (frame, {"coverage": 1}))
    config = load_config()
    config["xgboost_candidates"] = [dict(n_estimators=10, max_depth=2, learning_rate=0.05, min_child_weight=20)]
    first = training.train(tmp_path, tmp_path / "first", config)
    frame.loc[frame.valid_time >= pd.Timestamp("2025-07-01", tz="UTC"), "observed_c"] += 50
    second = training.train(tmp_path, tmp_path / "second", config)
    assert first["selected_model"] == second["selected_model"]
    assert first["validation_candidates"] == second["validation_candidates"]
    for name in ("ridge", "xgboost"):
        a = joblib.load(tmp_path / "first" / f"{name}.joblib")
        b = joblib.load(tmp_path / "second" / f"{name}.joblib")
        np.testing.assert_array_equal(training.predict(a, frame), training.predict(b, frame))
    capsys.readouterr()


@pytest.mark.parametrize("observed,predicted", [([], []), ([1, 2], [1]), ([np.nan], [1])])
def test_invalid_metrics_rejected(observed, predicted):
    with pytest.raises(ValueError):
        metrics(observed, predicted)


def test_rolling_splits_preserve_lengths_and_gaps():
    from pathlib import Path
    from meteo_mlops.config import load_config
    original = load_config()
    first = load_config(Path('experiments/pressure-rolling.json'), '2026-10-09')
    next_day = load_config(Path('experiments/pressure-rolling.json'), '2026-10-10')
    assert pd.Timestamp(first['splits']['test'][1]) == pd.Timestamp('2026-10-09T00:00Z')
    for name in ('train', 'validation', 'test'):
        a, b = map(pd.Timestamp, original['splits'][name])
        x, y = map(pd.Timestamp, first['splits'][name])
        assert y - x == b - a
        for index in (0, 1):
            assert pd.Timestamp(next_day['splits'][name][index]) - pd.Timestamp(first['splits'][name][index]) == pd.Timedelta(days=1)
    for left, right in (('train', 'validation'), ('validation', 'test')):
        assert pd.Timestamp(first['splits'][right][0]) - pd.Timestamp(first['splits'][left][1]) == pd.Timedelta(days=2)


def test_pressure_fetch_and_prepare(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from urllib.parse import parse_qs, urlparse
    from meteo_mlops import data
    from meteo_mlops.config import load_config
    config = load_config(Path('experiments/pressure-rolling.json'), '2026-10-09')
    requests = []
    monkeypatch.setattr(data, 'download', lambda url, path: requests.append(url) or {'url': url})
    data.fetch(tmp_path, config)
    query = parse_qs(urlparse(requests[-2]).query)
    assert query['hourly'] == ['temperature_2m_previous_day1,pressure_msl_previous_day1']
    assert query['end_date'] == ['2026-10-08']
    forecast = {'hourly_units': {data.FORECAST_VARIABLE: '°C', data.PRESSURE_VARIABLE: 'hPa'},
                'hourly': {'time': ['2026-10-08T00:00', '2026-10-08T01:00'],
                           data.FORECAST_VARIABLE: [10, 11], data.PRESSURE_VARIABLE: [1010, 1012]}}
    (tmp_path / 'forecast-2026.json').write_text(json.dumps(forecast))
    (tmp_path / 'observations-2026.csv').write_text('station,valid,tmpc\nNZWN,2026-10-08 00:00,11\nNZWN,2026-10-08 01:00,12\n')
    frame, _ = data.prepare(tmp_path, pressure=True)
    assert frame.pressure_msl_hpa.tolist() == [1010, 1012]
    forecast['hourly_units'][data.PRESSURE_VARIABLE] = 'Pa'
    (tmp_path / 'forecast-2026.json').write_text(json.dumps(forecast))
    with pytest.raises(ValueError, match='hPa'):
        data.prepare(tmp_path, pressure=True)
