"""Record/verify the same MLflow run, artifact and PVC identities across reboot.

Requires the AWS SSM API tunnel and .aws-k3s/kubeconfig.
"""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".aws-k3s/persistence.json"


def kubectl(*args, input=None):
    return subprocess.check_output([
        "kubectl", "--kubeconfig", str(ROOT / ".aws-k3s/kubeconfig"), "--context", "default",
        "-n", "meteo-mlops", *args,
    ], input=input, text=True)


def snapshot(run_id=None):
    code = '''
import hashlib, json, tempfile, urllib.request
from mlflow import MlflowClient
from mlflow.artifacts import download_artifacts
client = MlflowClient(tracking_uri='http://mlflow:5000')
'''
    if run_id:
        code += f"run = client.get_run({run_id!r})\n"
    else:
        code += '''
experiment = client.get_experiment_by_name('meteo/NZWN/gfs_global/temperature_2m/24h')
assert experiment is not None, 'No training experiment'
runs = client.search_runs([experiment.experiment_id], order_by=['attributes.start_time DESC'])
run = next(r for r in runs if r.info.status == 'FINISHED')
'''
    code += '''
assert run.info.status == 'FINISHED'
with tempfile.TemporaryDirectory() as tmp:
    path = download_artifacts(artifact_uri=f'runs:/{run.info.run_id}/bundle/model.joblib',
                              dst_path=tmp, tracking_uri='http://mlflow:5000')
    digest = hashlib.sha256(open(path, 'rb').read()).hexdigest()
model = client.get_registered_model('nzwn-gfs-temperature-24h')
for endpoint in ('health', 'ready'):
    assert urllib.request.urlopen('http://inference:8000/' + endpoint, timeout=10).status == 200
print(json.dumps({'run_id':run.info.run_id, 'metrics':run.data.metrics,
                  'model_sha256':digest, 'aliases':model.aliases}, sort_keys=True))
'''
    raw = kubectl("exec", "-i", "deployment/mlflow", "--", "python", "-", input=code)
    result = json.loads(raw.strip().splitlines()[-1])
    pvcs = json.loads(kubectl("get", "pvc", "-o", "json"))["items"]
    assert len(pvcs) == 2 and all(p["status"]["phase"] == "Bound" for p in pvcs)
    result["pvcs"] = {p["metadata"]["name"]: {"uid":p["metadata"]["uid"], "volume":p["spec"]["volumeName"]} for p in pvcs}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["snapshot", "verify"])
    args = parser.parse_args()
    if args.action == "snapshot":
        result = snapshot()
        STATE.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print("Recorded", json.dumps(result, sort_keys=True))
    else:
        before = json.loads(STATE.read_text())
        after = snapshot(before["run_id"])
        assert before == after, {"before":before, "after":after}
        print("Verified same run, metrics, artifact, aliases, PVCs and inference readiness:", json.dumps(after, sort_keys=True))
