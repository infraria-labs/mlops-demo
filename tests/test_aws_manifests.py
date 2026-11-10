"""Check AWS adaptations preserve workload/storage contracts of the local lab."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("render_aws", Path(__file__).parents[1] / "scripts/render-aws.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def test_aws_workloads_use_ecr_s3_and_persistent_metadata():
    outputs = {
        "repositories": {"value": {name: f"123.dkr.ecr.us-west-2.amazonaws.com/{name}" for name in ("inference", "training")}},
        "artifact_destination": {"value": "s3://demo/mlflow"},
        "region": {"value": "us-west-2"},
    }
    docs = renderer.render(outputs, "git-abcd")
    by_name = {(d["kind"], d["metadata"]["name"]): d for d in docs}
    mlflow = by_name["Deployment", "mlflow"]["spec"]["template"]["spec"]
    container = mlflow["containers"][0]
    assert container["command"][container["command"].index("--artifacts-destination") + 1] == "s3://demo/mlflow"
    assert "sqlite:////mlflow/mlflow.db" in container["command"]
    assert mlflow["volumes"][0]["persistentVolumeClaim"]["claimName"] == "mlflow-data"
    cron = by_name["CronJob", "training"]
    assert cron["spec"]["suspend"] is True
    training = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    assert training["image"].endswith("/training:git-abcd")
    assert training["imagePullPolicy"] == "IfNotPresent"
    assert "MLFLOW_ARTIFACT_ROOT" in by_name["ConfigMap", "runtime-config"]["data"]
    assert container["image"].endswith("/inference:git-abcd")
    for d in docs:
        if d["kind"] == "Service":
            assert d["spec"].get("type", "ClusterIP") == "ClusterIP"
    with pytest.raises(ValueError, match="tag"):
        renderer.render(outputs, "unsafe/tag")
