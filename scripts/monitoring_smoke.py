"""Check private monitoring APIs and every provisioned PromQL expression."""
import argparse
import json
from pathlib import Path
import subprocess
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def fetch(url, headers=None):
    with urlopen(Request(url, headers=headers or {}), timeout=15) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prometheus', default='http://127.0.0.1:9090')
    parser.add_argument('--grafana', default='http://127.0.0.1:3000')
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--context', required=True)
    args = parser.parse_args()
    targets = fetch(args.prometheus + '/api/v1/targets')['data']['activeTargets']
    jobs = {t['labels']['job'] for t in targets}
    assert {'inference', 'prometheus', 'cadvisor', 'kube-state-metrics'} <= jobs, jobs
    assert all(t['health'] == 'up' for t in targets), [(t['labels'], t['lastError']) for t in targets]
    dashboard = json.loads(Path('charts/meteo-monitoring/files/dashboard.json').read_text())
    for panel in dashboard['panels']:
        for target in panel['targets']:
            response = fetch(args.prometheus + '/api/v1/query?' + urlencode({'query': target['expr']}))
            assert response['status'] == 'success', panel['title']
    for query in ['meteo_model_info', 'meteo_predictions_total', 'kube_pod_status_ready', 'container_memory_working_set_bytes']:
        assert fetch(args.prometheus + '/api/v1/query?' + urlencode({'query': query}))['data']['result'], query
    def grafana(path):
        # Keep the admin credential inside the pod, including for AWS checks.
        result = subprocess.run([
            'kubectl', '--kubeconfig', args.kubeconfig, '--context', args.context,
            '-n', 'meteo-mlops', 'exec', 'deployment/monitoring-grafana', '--',
            'sh', '-c', 'curl -fsS --user "admin:$GF_SECURITY_ADMIN_PASSWORD" "http://127.0.0.1:3000$1"',
            'grafana-check', path,
        ], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    assert fetch(args.grafana + '/api/health')['database'] == 'ok'
    provisioned = grafana('/api/dashboards/uid/meteo-mlops')
    assert provisioned['meta']['provisioned']
    assert len(provisioned['dashboard']['panels']) == len(dashboard['panels'])
    datasource = grafana('/api/datasources/uid/prometheus/health')
    assert datasource['status'] == 'OK', datasource
    print(json.dumps({'targets': len(targets), 'jobs': sorted(jobs), 'dashboard_panels': len(dashboard['panels']),
                      'grafana': 'healthy and provisioned', 'queries': 'all valid'}))


if __name__ == '__main__':
    main()
