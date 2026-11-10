"""Submit and record one bounded run through a localhost lab API forward."""
import argparse
import json
from pathlib import Path
import time
from kfp import Client

parser = argparse.ArgumentParser()
parser.add_argument('--fail-evaluation', action='store_true')
parser.add_argument('--timeout', type=int, default=600)
parser.add_argument('--output', type=Path, default=Path('artifacts/kfp-lab'))
args = parser.parse_args()
client = Client(host='http://127.0.0.1:8888')
result = client.create_run_from_pipeline_package(
    '/tmp/meteo-kfp-pipeline.yaml',
    arguments={'fail_evaluation': str(args.fail_evaluation).lower()},
    run_name=f"meteo-lab-{'failure' if args.fail_evaluation else 'success'}-{int(time.time())}",
    experiment_name='isolated-meteo-lab', enable_caching=False,
)
args.output.mkdir(parents=True, exist_ok=True)
print(f'Run ID: {result.run_id}', flush=True)
(args.output / 'last-run-id.txt').write_text(result.run_id + '\n')
try:
    result.wait_for_run_completion(timeout=args.timeout)
finally:
    run = client.get_run(result.run_id).to_dict()
    (args.output / f'{result.run_id}.json').write_text(json.dumps(run, indent=2, default=str))
print(json.dumps({k: run.get(k) for k in ('run_id', 'state', 'created_at', 'finished_at')}, indent=2, default=str))
expected = 'FAILED' if args.fail_evaluation else 'SUCCEEDED'
if run.get('state') != expected:
    raise RuntimeError(f"Expected {expected}, got {run.get('state')}; inspect exported run metadata")
