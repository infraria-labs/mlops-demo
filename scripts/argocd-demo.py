"""Manual Argo sync, status and a reversible ConfigMap drift exercise."""
import argparse
import json
import os
import subprocess
import time


def kubectl(*args):
    if not os.environ.get('KUBECONFIG'):
        raise ValueError('Set KUBECONFIG explicitly to the demo cluster')
    return subprocess.check_output(['kubectl', *args], text=True)


def app():
    return json.loads(kubectl('-n', 'argocd', 'get', 'application', 'meteo-mlops', '-o', 'json'))


def refresh():
    kubectl('-n', 'argocd', 'annotate', 'application', 'meteo-mlops',
            'argocd.argoproj.io/refresh=hard', '--overwrite')


def wait_for(predicate, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = app()
        if predicate(state):
            return state
        time.sleep(3)
    raise TimeoutError('Inspect Application conditions and operationState before retrying')


def status():
    state = app().get('status', {})
    print(json.dumps({key: state.get(key) for key in
                      ('sync', 'health', 'conditions', 'operationState')}, indent=2))


def sync():
    state = app()
    if state.get('operation'):
        raise RuntimeError('An operation is already pending; inspect it before retrying')
    previous = state.get('status', {}).get('operationState', {}).get('startedAt')
    # Pin the operation to the Git revision just compared by the controller.
    revision = state.get('status', {}).get('sync', {}).get('revision')
    if not revision:
        raise RuntimeError('No compared Git revision; inspect repository connection/conditions')
    patch = {'operation': {'initiatedBy': {'username': 'demo-operator'},
                           'sync': {'revision': revision, 'prune': False}}}
    kubectl('-n', 'argocd', 'patch', 'application', 'meteo-mlops',
            '--type', 'merge', '-p', json.dumps(patch))
    finished = wait_for(lambda a: (
        a.get('status', {}).get('operationState', {}).get('startedAt') != previous
        and a.get('status', {}).get('operationState', {}).get('phase')
        in ('Succeeded', 'Failed', 'Error')))
    operation = finished['status']['operationState']
    if operation['phase'] != 'Succeeded':
        raise RuntimeError(operation.get('message', operation['phase']))
    wait_for(lambda a: a.get('status', {}).get('sync', {}).get('status') == 'Synced'
             and a.get('status', {}).get('health', {}).get('status') == 'Healthy')
    status()


def drift():
    state = app()
    if state['spec'].get('syncPolicy', {}).get('automated') is not None:
        raise RuntimeError('This observable drift exercise requires manual sync')
    if state.get('status', {}).get('sync', {}).get('status') != 'Synced':
        raise RuntimeError('Start from Synced desired state')
    config = json.loads(kubectl('-n', 'meteo-mlops', 'get', 'configmap',
                               'runtime-config', '-o', 'json'))
    original = config['data']['MPLCONFIGDIR']
    try:
        kubectl('-n', 'meteo-mlops', 'patch', 'configmap', 'runtime-config',
                '--type', 'merge', '-p', json.dumps({'data': {'MPLCONFIGDIR': '/tmp/gitops-drift'}}))
        refresh()
        wait_for(lambda a: a.get('status', {}).get('sync', {}).get('status') == 'OutOfSync', 180)
        print('Observed OutOfSync after a live ConfigMap mutation')
        sync()
        restored = json.loads(kubectl('-n', 'meteo-mlops', 'get', 'configmap',
                                     'runtime-config', '-o', 'json'))
        assert restored['data']['MPLCONFIGDIR'] == original
        print('Argo restored the Git value; Application is Synced/Healthy')
    except Exception:
        # Restore the single field even if reconciliation fails.
        kubectl('-n', 'meteo-mlops', 'patch', 'configmap', 'runtime-config',
                '--type', 'merge', '-p', json.dumps({'data': {'MPLCONFIGDIR': original}}))
        refresh()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'refresh', 'sync', 'drift'])
    args = parser.parse_args()
    {'status': status, 'refresh': refresh, 'sync': sync, 'drift': drift}[args.action]()


if __name__ == '__main__':
    main()
