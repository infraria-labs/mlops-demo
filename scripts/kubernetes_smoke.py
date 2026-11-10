"""Exercise a deployed lab; creates a training Job and restarts pods.

For AWS, set KUBECONFIG, KUBE_CONTEXT and AWS_ARTIFACT_DESTINATION explicitly.
"""
import json
import os
from pathlib import Path
import subprocess
import time

os.environ.setdefault("KUBECONFIG", str(Path(".k3d/kubeconfig").resolve()))
context = os.environ.get("KUBE_CONTEXT", "k3d-meteo-mlops")


def kubectl(*args, input=None):
    return subprocess.run(["kubectl", "--context", context, "-n", "meteo-mlops", *args], input=input,
                          text=True, capture_output=True, check=True).stdout


def remote(deployment, code):
    return kubectl("exec", "-i", f"deployment/{deployment}", "--", "python", "-", input=code)


api_check = '''
import json, urllib.request
for endpoint in ('health', 'ready'):
    response = urllib.request.urlopen('http://inference:8000/' + endpoint, timeout=10)
    assert response.status == 200
request = urllib.request.Request('http://inference:8000/predict',
    data=json.dumps({'forecasts': [{'valid_time': '2026-01-01T12:00:00Z', 'forecast_c': 18.0}]}).encode(),
    headers={'Content-Type': 'application/json'})
result=json.load(urllib.request.urlopen(request, timeout=10))
print(json.dumps(result))
'''
print(remote("inference", api_check))
# A failed new image must leave the old replica serving; undo restores it.
try:
    kubectl("set", "image", "deployment/inference", "inference=meteo-inference:missing-smoke")
    try:
        kubectl("rollout", "status", "deployment/inference", "--timeout=15s")
    except subprocess.CalledProcessError:
        pass  # The missing imported image must prevent this rollout.
    else:
        raise AssertionError("Missing-image rollout unexpectedly succeeded")
    print(remote("mlflow", api_check))
finally:
    kubectl("rollout", "undo", "deployment/inference")
kubectl("rollout", "status", "deployment/inference", "--timeout=300s")
print(remote("inference", api_check))
job = f"smoke-training-{int(time.time())}"
kubectl("create", "job", job, "--from=cronjob/training")
try:
    kubectl("wait", "--for=condition=complete", f"job/{job}", "--timeout=1800s")
finally:
    print(kubectl("logs", f"job/{job}"))
# Verify a completed run and its bundle over the Service API, then compare after restart.
tracking_check = '''
import hashlib, json, tempfile
from mlflow import MlflowClient
from mlflow.artifacts import download_artifacts
client=MlflowClient(tracking_uri='http://mlflow:5000')
experiment=client.get_experiment_by_name('meteo/NZWN/gfs_global/temperature_2m/24h')
runs=client.search_runs([experiment.experiment_id], order_by=['attributes.start_time DESC'])
run=next(r for r in runs if r.info.status == 'FINISHED')
with tempfile.TemporaryDirectory() as tmp:
    path=download_artifacts(artifact_uri=f'runs:/{run.info.run_id}/bundle/model.joblib',
                            dst_path=tmp, tracking_uri='http://mlflow:5000')
    digest=hashlib.sha256(open(path,'rb').read()).hexdigest()
print(json.dumps({'run_id':run.info.run_id,'metrics':run.data.metrics,'model_sha256':digest},sort_keys=True))
'''
if os.environ.get("AWS_ARTIFACT_DESTINATION"):
    tracking_check += "\ndestination = " + repr(os.environ["AWS_ARTIFACT_DESTINATION"]) + "\n"
    tracking_check += '''
import boto3
from urllib.parse import urlparse
uri = urlparse(destination)
s3 = boto3.client('s3')
matching = []
for page in s3.get_paginator('list_objects_v2').paginate(Bucket=uri.netloc, Prefix=uri.path.lstrip('/') + '/'):
    matching.extend(o['Key'] for o in page.get('Contents', [])
                    if '/' + run.info.run_id + '/' in o['Key'] and o['Key'].endswith('/bundle/model.joblib'))
assert len(matching) == 1, matching
body = s3.get_object(Bucket=uri.netloc, Key=matching[0])['Body'].read()
assert hashlib.sha256(body).hexdigest() == digest
print(json.dumps({'run_id':run.info.run_id,'metrics':run.data.metrics,'model_sha256':digest},sort_keys=True))
'''
before = remote("mlflow", tracking_check).strip().splitlines()[-1]
kubectl("rollout", "restart", "deployment/mlflow")
kubectl("rollout", "status", "deployment/mlflow", "--timeout=300s")
after = remote("mlflow", tracking_check).strip().splitlines()[-1]
assert json.loads(before) == json.loads(after), (before, after)
print("Verified training, rollout/rollback and MLflow metadata/artifact persistence:", after)
