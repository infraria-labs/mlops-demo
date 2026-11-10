"""Render the local workloads for AWS without changing the local lab.

Read Terraform JSON outputs from stdin; write manifests to stdout.
"""
import argparse
import json
from pathlib import Path
import re
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]


def render(outputs, tag):
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        raise ValueError("Invalid immutable ECR image tag")
    repositories = outputs["repositories"]["value"]
    destination = outputs["artifact_destination"]["value"]
    region = outputs["region"]["value"]
    if not destination.startswith("s3://"):
        raise ValueError("Expected S3 artifact destination")
    documents = []
    for name in ("namespace", "config", "storage", "mlflow", "inference", "training-cronjob"):
        for doc in yaml.safe_load_all((ROOT / "k8s/local" / f"{name}.yaml").read_text()):
            if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "runtime-config":
                doc["data"]["AWS_DEFAULT_REGION"] = region
            if doc["kind"] in ("Deployment", "CronJob"):
                pod = (doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
                       if doc["kind"] == "CronJob" else doc["spec"]["template"]["spec"])
                for container in pod["containers"]:
                    repo = "training" if container["name"] == "training" else "inference"
                    container["image"] = f'{repositories[repo]}:{tag}'
                    container["imagePullPolicy"] = "IfNotPresent"
                    if container["name"] == "mlflow":
                        command = container["command"]
                        command[command.index("--artifacts-destination") + 1] = destination
                        container["env"] = [{"name": "AWS_DEFAULT_REGION", "value": region}]
                # EBS hosts the entire k3s data directory, including local-path PVCs.
            documents.append(doc)
    return documents


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    yaml.safe_dump_all(render(json.load(sys.stdin), args.tag), sys.stdout, sort_keys=False)
