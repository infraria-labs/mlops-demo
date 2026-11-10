"""GitOps release isolation and real Helm/Argo rendering contracts."""
import importlib.util
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

spec = importlib.util.spec_from_file_location('gitops_release', 'scripts/gitops_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_image_release_preserves_model_configuration(tmp_path):
    path = tmp_path / 'values.yaml'
    original = Path('gitops/aws/values.yaml').read_text()
    path.write_text(original)
    before = yaml.safe_load(original)
    assert release.update(path, 'b' * 40)
    after = yaml.safe_load(path.read_text())
    for target in ('inference', 'training'):
        assert after['images'][target]['tag'] == 'b' * 40
        before['images'][target]['tag'] = 'b' * 40
    assert after == before
    assert not release.update(path, 'b' * 40)


@pytest.mark.parametrize('sha', ['latest', 'abc123', 'a' * 39, 'a' * 40 + '\n'])
def test_invalid_release_cannot_write(tmp_path, sha):
    path = tmp_path / 'values.yaml'
    original = Path('gitops/aws/values.yaml').read_text()
    path.write_text(original)
    with pytest.raises(ValueError):
        release.update(path, sha)
    assert path.read_text() == original


def test_unexpected_values_fail_without_mutation(tmp_path):
    path = tmp_path / 'values.yaml'
    original = Path('gitops/aws/values.yaml').read_text().replace('    tag:', '    tag: # unexpected\n      ')
    path.write_text(original)
    with pytest.raises((ValueError, yaml.YAMLError)):
        release.update(path, 'b' * 40)
    assert path.read_text() == original


@pytest.mark.skipif(not shutil.which('helm'), reason='Helm required')
def test_argo_hooks_and_persistent_data():
    app = yaml.safe_load(Path('gitops/argocd/application.yaml').read_text())
    source = app['spec']['source']
    values = Path(source['path']) / source['helm']['valueFiles'][0]
    result = subprocess.check_output([
        'helm', 'template', source['helm']['releaseName'], source['path'],
        '-n', app['spec']['destination']['namespace'], '-f', str(values),
    ], text=True)
    docs = list(yaml.safe_load_all(result))
    job = next(d for d in docs if d['kind'] == 'Job')
    assert job['metadata']['annotations']['argocd.argoproj.io/hook'] == 'Sync'
    assert job['metadata']['annotations']['argocd.argoproj.io/hook-delete-policy'] == 'BeforeHookCreation,HookSucceeded'
    assert 'ttlSecondsAfterFinished' not in job['spec']
    for pvc in (d for d in docs if d['kind'] == 'PersistentVolumeClaim'):
        assert pvc['metadata']['annotations']['argocd.argoproj.io/sync-options'] == 'Prune=false,Delete=false'
    assert not any(d['kind'] == 'Secret' for d in docs)
    assert 'automated' not in app['spec']['syncPolicy']
    assert not app['metadata'].get('finalizers')


def test_ci_has_no_direct_cluster_deployment():
    workflow = yaml.load(Path('.github/workflows/inference.yml').read_text(), Loader=yaml.BaseLoader)
    job = workflow['jobs']['release']
    assert "vars.ENABLE_AWS_RELEASE == 'true'" in job['if']
    assert "github.event_name != 'pull_request'" in job['if']
    steps = '\n'.join(str(step) for step in job['steps'])
    assert 'scripts/gitops_release.py' in steps
    assert 'gh pr create' in steps
    assert all(forbidden not in steps for forbidden in ('ci_deploy', 'kubectl', 'helm upgrade', 'ssm'))
    assert 'gitops/**' in workflow['on']['push']['paths-ignore']
    assert 'INSTANCE_ID' not in job['env']
    assert job['permissions']['pull-requests'] == 'write'
