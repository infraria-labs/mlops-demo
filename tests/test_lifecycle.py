import json

import joblib
import mlflow
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sklearn.dummy import DummyRegressor

from meteo_mlops.api import create_app
from meteo_mlops.config import load_config
from meteo_mlops.features import FEATURES, PRESSURE_FEATURES, features
from meteo_mlops.lifecycle import gate, materialize, promote, main
from meteo_mlops.tracking import MODEL, configure


def scores(rmse, mae=0.5, bias=0.1):
    return dict(rmse_c=rmse, mae_c=mae, bias_c=bias)


@pytest.mark.parametrize('candidate,approved', [
    (scores(0.8), True), (scores(0.99), False), (scores(1.1), False),
    (scores(0.8, mae=0.6), False), (scores(0.8, bias=-0.2), False),
    (scores(float('nan')), False), (scores(float('inf')), False), (scores(-1), False),
])
def test_gate(candidate, approved):
    assert gate(candidate, scores(1))['approved'] is approved


@pytest.mark.parametrize("rolling", [False, True])
def test_promotion_serving_rejection_and_rollback(tmp_path, monkeypatch, rolling):
    from pathlib import Path
    config = load_config(Path("experiments/pressure-rolling.json"), "2026-01-01") if rolling else load_config()
    client = configure()
    client.create_registered_model(MODEL)
    frame = pd.DataFrame({'valid_time': pd.date_range('2025-07-01', periods=1000, freq='h', tz='UTC'),
                          'forecast_c': 10.0, 'observed_c': 11.0})
    frame['pressure_msl_hpa'] = 1013.0
    versions = []
    for correction in (0.5, 0.9, -1):
        output = tmp_path / str(correction)
        output.mkdir()
        names = FEATURES if correction == 0.5 else PRESSURE_FEATURES
        artifact = dict(schema_version=2, features=names, station='NZWN', horizon_hours=24,
                        forecast_model='gfs_global', model_name='fixture', config=config,
                        model=DummyRegressor(strategy='constant', constant=correction).fit(features(frame, names), frame.observed_c))
        joblib.dump(artifact, output / 'model.joblib')
        frame.to_csv(output / 'test-predictions.csv', index=False)
        with mlflow.start_run() as run:
            mlflow.log_artifacts(str(output), 'bundle')
        versions.append(client.create_model_version(MODEL, run.info.artifact_uri + '/bundle', run.info.run_id).version)
    assert promote(client, versions[0])['approved']
    assert promote(client, versions[1])['champion'] == versions[0]
    assert client.get_model_version_by_alias(MODEL, 'previous').version == versions[0]
    with pytest.raises(ValueError, match='regression'):
        promote(client, versions[2])
    assert client.get_model_version_by_alias(MODEL, 'champion').version == versions[1]
    path = materialize(client, versions[1], tmp_path / 'serving')
    with TestClient(create_app(path / 'model.joblib')) as api:
        assert api.get('/ready').json()['registry_version'] == str(versions[1])
        result = api.post('/predict', json={'forecasts': [{'valid_time': '2026-01-01T12:00Z', 'forecast_c': 10.0, 'pressure_msl_hpa': 1013.0}]})
        assert result.json()['forecasts'][0]['corrected_c'] == pytest.approx(10.9)
    monkeypatch.setattr('sys.argv', ['meteo-lifecycle', 'rollback', str(versions[0])])
    main()
    assert client.get_model_version_by_alias(MODEL, 'champion').version == versions[0]
    rolled = materialize(client, versions[0], tmp_path / 'rollback')
    with TestClient(create_app(rolled / 'model.joblib')) as api:
        assert api.get('/ready').json()['registry_version'] == str(versions[0])
    provenance = json.loads((path / 'registry.json').read_text())
    provenance['sha256'] = 'invalid'
    (path / 'registry.json').write_text(json.dumps(provenance))
    with pytest.raises(ValueError, match='checksum'):
        with TestClient(create_app(path / 'model.joblib')):
            pass


def test_configured_fetch_window(tmp_path, monkeypatch):
    from meteo_mlops import data
    config = load_config()
    config['splits'] = {'train': ['2025-02-01', '2025-08-01'],
                        'validation': ['2025-08-03', '2025-10-01'],
                        'test': ['2025-10-03', '2026-02-01']}
    requests = []
    monkeypatch.setattr(data, 'download', lambda url, path: requests.append(url) or {'url': url})
    data.fetch(tmp_path, config)
    assert len(requests) == 4
    assert 'start_date=2025-02-01' in requests[0]
    assert 'end_date=2026-01-31' in requests[2]
    assert 'year2=2026&month2=2&day2=1' in requests[3]


def test_failed_run_cannot_be_evaluated(tmp_path):
    from meteo_mlops.lifecycle import evaluate_candidate
    client = configure()
    client.create_registered_model(MODEL)
    with pytest.raises(RuntimeError):
        with mlflow.start_run() as run:
            raise RuntimeError('failed training')
    version = client.create_model_version(MODEL, run.info.artifact_uri, run.info.run_id).version
    with pytest.raises(ValueError, match='finished'):
        evaluate_candidate(client, version)
    assert not client.get_registered_model(MODEL).aliases
