"""Exercise the training image with deterministic data and no archive network access."""
import argparse
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    # Test imports/CLI under the production UID, and use the same synthetic
    # training integration fixture as pytest inside the container.
    subprocess.run(['docker', 'run', '--rm', '--read-only', '--cap-drop=ALL',
                    '--security-opt=no-new-privileges', args.image, '--help'], check=True)
    script = """import os
from pathlib import Path
import numpy as np, pandas as pd, joblib
from meteo_mlops import training
from meteo_mlops.config import load_config
os.chdir('/tmp')
__import__('shutil').copy('/app/uv.lock', '/tmp/uv.lock')
os.environ['MPLCONFIGDIR'] = '/tmp/matplotlib'
os.environ['MLFLOW_TRACKING_URI'] = 'sqlite:////tmp/mlflow.db'
os.environ['MLFLOW_ARTIFACT_ROOT'] = 'file:///tmp/mlflow-artifacts'
frame = pd.DataFrame({'valid_time': pd.date_range('2024-01-01', '2025-12-31T23:00', freq='h', tz='UTC')})
frame['forecast_c'] = 12 + np.sin(np.arange(len(frame)) / 1000)
frame['observed_c'] = frame.forecast_c + 1 + np.sin(frame.valid_time.dt.hour * 2 * np.pi / 24)
training.prepare = lambda root: (frame, {'coverage': 1.0})
config = load_config()
config['ridge_alphas'] = [1.0]
config['xgboost_candidates'] = [dict(n_estimators=10, max_depth=2, learning_rate=0.05, min_child_weight=20)]
root = Path('/tmp/raw'); root.mkdir()
(root / 'input.csv').write_text('synthetic CI fixture')
output = Path('/tmp/output')
training.train(root, output, config)
model = joblib.load(output / 'model.joblib')
assert np.isfinite(training.predict(model, frame)).all()
assert (output / 'tracking.json').exists()
assert Path('/app/uv.lock').is_file()
assert os.getuid() == 10001
"""
    subprocess.run(['docker', 'run', '--rm', '--read-only', '--cap-drop=ALL',
                    '--security-opt=no-new-privileges', '--tmpfs', '/tmp',
                    '--entrypoint', 'python', args.image, '-c', script], check=True)


if __name__ == '__main__':
    main()
