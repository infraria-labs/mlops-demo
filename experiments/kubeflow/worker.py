"""Container adapter for the isolated KFP lab; never promotes a model."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from meteo_mlops.lifecycle import evaluate_candidate
from meteo_mlops.tracking import configure

parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['train', 'evaluate'])
parser.add_argument('--version')
parser.add_argument('--output', required=True)
parser.add_argument('--fail', default='false')
args = parser.parse_args()
Path('/tmp/work').mkdir(exist_ok=True)
os.chdir('/tmp/work')
output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
if args.stage == 'train':
    shutil.copy('/app/uv.lock', 'uv.lock')
    subprocess.run(['meteo-baseline', 'train', '--data', '/snapshot', '--output', 'bundle'], check=True)
    tracking = json.loads(Path('bundle/tracking.json').read_text())
    output.write_text(str(tracking['model_version']))
    print(json.dumps(tracking))
else:
    decision = evaluate_candidate(configure(), args.version)
    output.write_text(json.dumps(decision, indent=2))
    print(json.dumps(decision))
    if args.fail == 'true':
        raise RuntimeError('Intentional evaluation failure after recording evidence')
