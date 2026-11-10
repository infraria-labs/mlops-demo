import json
import shutil
import subprocess

import joblib
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
import pytest
import yaml

from meteo_mlops.api import create_app
from test_api import model_path as serving_model_path


@pytest.fixture
def model_path(tmp_path):
    return serving_model_path.__wrapped__(tmp_path)


def samples(client):
    response = client.get('/metrics')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/plain')
    return [s for f in text_string_to_metric_families(response.text) for s in f.samples]


def test_metrics_count_validation_failure_and_prediction_rows(model_path):
    with TestClient(create_app(model_path)) as client:
        client.post('/predict', json={'forecasts': []})
        client.get('/unknown/customer-secret')
        client.post('/predict', json={'forecasts': [{'valid_time': '2026-01-01T00:00Z', 'forecast_c': 18}] * 2})
        rows = samples(client)
        assert next(s.value for s in rows if s.name == 'meteo_predictions_total') == 2
        assert any(s.name == 'meteo_errors_total' and s.labels == {'route': '/predict', 'status': '422'} and s.value == 1 for s in rows)
        assert any(s.name == 'meteo_request_duration_seconds_count' and s.labels['route'] == '/predict' and s.value == 2 for s in rows)
        assert all('customer-secret' not in str(s.labels) for s in rows)
        assert any(s.name == 'meteo_model_info' and s.labels['registry_version'] == 'unregistered' for s in rows)
        assert not any(s.name == 'meteo_holdout_quality' for s in rows)
        # Independent apps/tests must not share counters or register duplicate collectors.
    with TestClient(create_app(model_path)) as client:
        assert next(s.value for s in samples(client) if s.name == 'meteo_predictions_total') == 0


def test_model_quality_is_from_served_artifact(model_path):
    artifact = joblib.load(model_path)
    artifact['trained_timestamp_seconds'] = 100
    artifact['holdout_quality'] = {'raw': {'rmse_c': 2, 'bias_c': -1}, 'mean_bias_correction': {'rmse_c': 1, 'rolling_bias_c': .2, 'rmse_skill': None}}
    joblib.dump(artifact, model_path)
    app = create_app(model_path)
    with TestClient(app) as client:
        rows = samples(client)
        assert any(s.name == 'meteo_holdout_quality' and s.labels == {'method': 'raw', 'metric': 'rmse_c'} and s.value == 2 for s in rows)
        def broken(_):
            raise RuntimeError('failure')
        app.state.artifact['model'].predict = broken
        client.post('/predict', json={'forecasts': [{'valid_time': '2026-01-01T00:00Z', 'forecast_c': 18}]})
        assert any(s.name == 'meteo_errors_total' and s.labels['status'] == '500' for s in samples(client))
        assert next(s.value for s in samples(client) if s.name == 'meteo_predictions_total') == 0


@pytest.mark.skipif(shutil.which('helm') is None, reason='Helm required')
def test_monitoring_render_and_queries():
    subprocess.run(['helm', 'lint', 'charts/meteo-monitoring', '--strict'], check=True)
    result = subprocess.run(['helm', 'template', 'monitoring', 'charts/meteo-monitoring', '-n', 'demo'], check=True, capture_output=True, text=True)
    docs = list(yaml.safe_load_all(result.stdout))
    config = next(d for d in docs if d['kind'] == 'ConfigMap')['data']
    scrape = yaml.safe_load(config['prometheus.yaml'])
    assert {s['job_name'] for s in scrape['scrape_configs']} == {'inference', 'prometheus', 'kube-state-metrics', 'cadvisor'}
    assert scrape['scrape_configs'][0]['kubernetes_sd_configs'][0]['namespaces']['names'] == ['demo']
    dashboard = json.loads(config['dashboard.json'])
    assert len(dashboard['panels']) == 22
    assert all(p['datasource']['uid'] == 'prometheus' for p in dashboard['panels'])
    assert all(d['spec']['type'] == 'ClusterIP' for d in docs if d['kind'] == 'Service')
    assert not any(d['kind'] in ('Ingress', 'Secret') for d in docs)
    deployments = [d for d in docs if d['kind'] == 'Deployment']
    assert len(deployments) == 3
    assert all(d['spec']['template']['spec']['containers'][0]['resources']['limits']['memory'] for d in deployments)


@pytest.mark.parametrize("aware", [False, True])
def test_verification_updates_and_rejects_wrong_model(model_path, tmp_path, monkeypatch, aware):
    import numpy as np
    import pandas as pd
    from meteo_mlops.quality import verify
    artifact = joblib.load(model_path)
    artifact['config'] = {'splits': {'validation': ['2024-01-01T00:00:00Z', '2025-01-01T00:00:00Z'] if aware else ['2024-01-01', '2025-01-01']}}
    joblib.dump(artifact, model_path)
    frame = pd.DataFrame({'valid_time': pd.date_range('2025-02-01', periods=168, freq='h', tz='UTC'),
                          'forecast_c': np.full(168, 10.), 'observed_c': np.full(168, 12.5)})
    csv = tmp_path / 'matched.csv'
    frame.to_csv(csv, index=False)
    output = tmp_path / 'quality.json'
    payload = verify(model_path, csv, output)
    assert payload['quality']['corrected']['rmse_c'] == 0
    assert payload['quality']['raw']['rolling_bias_c'] == -2.5
    monkeypatch.setenv('QUALITY_PATH', str(output))
    with TestClient(create_app(model_path)) as client:
        assert next(s.value for s in samples(client) if s.name == 'meteo_verification_available') == 1
        payload['sha256'] = 'another-model'
        output.write_text(json.dumps(payload))
        rows = samples(client)
        assert next(s.value for s in rows if s.name == 'meteo_verification_available') == 0
        assert not any(s.name == 'meteo_verification_quality' for s in rows)
    frame.loc[1, 'valid_time'] = frame.loc[0, 'valid_time']
    frame.to_csv(csv, index=False)
    with pytest.raises(ValueError, match='unique'):
        verify(model_path, csv, output)
    frame['valid_time'] = pd.date_range('2024-02-01', periods=168, freq='h', tz='UTC')
    frame.to_csv(csv, index=False)
    with pytest.raises(ValueError, match='embargo'):
        verify(model_path, csv, output)
