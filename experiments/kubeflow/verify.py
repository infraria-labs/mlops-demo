"""Compare lab versions with the fixed local reference; no promotion or writes."""
import argparse
import json
import math
from pathlib import Path
from mlflow import MlflowClient

parser = argparse.ArgumentParser()
parser.add_argument('versions', nargs='+')
parser.add_argument('--reference', type=Path, default=Path('artifacts/kfp-lab/reference/bundle'))
args = parser.parse_args()
client = MlflowClient(tracking_uri='http://127.0.0.1:5001')
model = 'nzwn-gfs-temperature-24h'
assert client.get_registered_model(model).aliases == {}, 'Lab must not promote models'
reference = json.loads((args.reference / 'metadata.json').read_text())
metrics = json.loads((args.reference / 'metrics.json').read_text())
records = []
for version in args.versions:
    registered = client.get_model_version(model, version)
    destination = Path('artifacts/kfp-lab') / f'version-{version}'
    destination.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(Path(client.download_artifacts(registered.run_id, 'bundle/metadata.json', str(destination))).read_text())
    scores = json.loads(Path(client.download_artifacts(registered.run_id, 'bundle/metrics.json', str(destination))).read_text())
    for key in ('config', 'data_sha256', 'uv_lock_sha256'):
        assert metadata[key] == reference[key], f'{version}: {key} differs'
    assert scores['selected_model'] == metrics['selected_model']
    for split in ('train', 'validation', 'test'):
        assert scores['splits'][split]['n'] == metrics['splits'][split]['n']
        for method in ('raw', 'mean_bias_correction', 'ridge', 'xgboost'):
            for key in ('rmse_c', 'mae_c', 'bias_c'):
                assert math.isclose(scores['splits'][split][method][key], metrics['splits'][split][method][key], rel_tol=0, abs_tol=1e-8), (version, split, method, key)
    records.append({'version': version, 'run_id': registered.run_id, 'selected_model': scores['selected_model'], 'test': scores['splits']['test'][scores['selected_model']]})
Path('artifacts/kfp-lab/comparison.json').write_text(json.dumps(records, indent=2))
print(json.dumps(records, indent=2))
print('Snapshot, configuration, lockfile, selection and all split metrics match; aliases remain empty.')
