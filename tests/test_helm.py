"""Validate rendered workloads for both labs and fail closed on mutable tags."""
import shutil
import subprocess

import pytest
import yaml

HELM = shutil.which('helm')
pytestmark = pytest.mark.skipif(HELM is None, reason='Helm is required for chart integration checks')
CHART = 'charts/meteo-mlops'


def render(*args):
    result = subprocess.run([HELM, 'template', 'meteo-mlops', CHART, '-n', 'meteo-mlops', *args],
                            capture_output=True, text=True, check=True)
    return list(yaml.safe_load_all(result.stdout))


@pytest.mark.parametrize('environment', ['local', 'aws'])
def test_environment_workloads(environment):
    args = ['-f', f'{CHART}/values-{environment}.yaml']
    if environment == 'aws':
        for key, value in {
            'images.inference.repository': 'registry/demo/inference',
            'images.training.repository': 'registry/demo/training',
            'images.inference.tag': 'a' * 40,
            'images.training.tag': 'a' * 40,
            'mlflow.artifactDestination': 's3://demo/mlflow',
        }.items():
            args += ['--set-string', f'{key}={value}']
    docs = render(*args)
    workloads = {d['metadata']['name']: d for d in docs if d['kind'] in ('Deployment', 'CronJob')}
    assert set(workloads) == {'inference', 'mlflow', 'training'}
    assert all(d['metadata']['namespace'] == 'meteo-mlops' for d in docs)
    inference = workloads['inference']['spec']['template']['spec']
    container = inference['containers'][0]
    assert container['readinessProbe']['httpGet']['path'] == '/ready'
    assert container['livenessProbe']['httpGet']['path'] == '/health'
    assert inference['securityContext']['runAsUser'] == 10001
    assert not inference['automountServiceAccountToken']
    assert container['securityContext']['readOnlyRootFilesystem']
    assert workloads['training']['spec']['suspend'] is True
    training = workloads['training']['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]
    assert 'meteo-baseline run' in training['command'][-1]
    assert training['image'].startswith('registry/demo/training:' if environment == 'aws' else 'meteo-training:')
    assert container['imagePullPolicy'] == ('IfNotPresent' if environment == 'aws' else 'Never')
    pvcs = [d for d in docs if d['kind'] == 'PersistentVolumeClaim']
    assert len(pvcs) == 2
    assert all(d['metadata']['annotations']['helm.sh/resource-policy'] == 'keep' for d in pvcs)
    assert not any(d['kind'] == 'Secret' for d in docs)


def test_explicit_training_job():
    docs = render('--set', 'training.job.enabled=true', '--set', 'training.job.name=training-test')
    job = next(d for d in docs if d['kind'] == 'Job' and d['metadata']['name'] == 'training-test')
    assert job['metadata']['name'] == 'training-test'
    assert job['spec']['backoffLimit'] == 0


@pytest.mark.parametrize('args', [
    ['--set', 'images.inference.tag=latest'],
    ['--set', 'images.training.tag=latest'],
    ['-f', f'{CHART}/values-aws.yaml', '--set', 'images.inference.repository='],
])
def test_unsafe_or_unconfigured_images_rejected(args):
    result = subprocess.run([HELM, 'template', 'demo', CHART, *args], capture_output=True)
    assert result.returncode != 0


def test_lint():
    subprocess.run([HELM, 'lint', CHART, '--strict'], check=True)


def test_pinned_registry_serving():
    docs = render('--set-string', 'inference.registryVersion=7')
    deployment = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'inference')
    spec = deployment['spec']['template']['spec']
    assert spec['initContainers'][0]['command'] == ['meteo-lifecycle', 'materialize', '7', '--output', '/models']
    assert spec['volumes'][0] == {'name': 'model', 'emptyDir': {}}


def test_eks_identity_isolated_to_mlflow():
    docs = render('-f', 'gitops/eks/values.yaml')
    accounts = [d for d in docs if d['kind'] == 'ServiceAccount']
    assert [d['metadata']['name'] for d in accounts] == ['mlflow']
    assert accounts[0]['automountServiceAccountToken'] is False
    deployments = {d['metadata']['name']: d for d in docs if d['kind'] == 'Deployment'}
    assert deployments['mlflow']['spec']['template']['spec']['serviceAccountName'] == 'mlflow'
    assert deployments['inference']['spec']['template']['spec'].get('serviceAccountName', 'default') == 'default'
    training = next(d for d in docs if d['kind'] == 'CronJob')
    assert training['spec']['jobTemplate']['spec']['template']['spec'].get('serviceAccountName', 'default') == 'default'
    pvcs = [d for d in docs if d['kind'] == 'PersistentVolumeClaim']
    assert all(d['spec']['storageClassName'] == 'eks-gp3' for d in pvcs)
    assert not any(d['kind'] == 'Secret' for d in docs)
    assert all(d['spec']['type'] == 'ClusterIP' if 'type' in d['spec'] else True
               for d in docs if d['kind'] == 'Service')
