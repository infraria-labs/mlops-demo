# Operations workflow and command reference

Run commands from the repository root. Replace uppercase placeholders with recorded values. Each terminal needs its own environment exports; a port-forward or SSM tunnel occupies that terminal until Ctrl-C. Commands here operate existing environments; first-time installation is in the stage guides.

## Read the stages in order

| Stage | Guide | What you should understand or verify |
| --- | --- | --- |
| 1. Data and baseline | [Baseline](baseline.md), [results](results.md), [README](../README.md) | NZWN METAR observations, GFS 24-hour offset, temporal splits, validation selection and reproducible artifacts |
| 2. API and container | [README](../README.md#run-the-example-locally-inf-49-inf-50-inf-51) | Trusted model loads once; readiness and prediction identify its checksum |
| 3. Tracking | [MLflow](mlflow.md) | Run ID, numeric registry version, artifact bundle and manual aliases |
| 4. Local Kubernetes | [k3d](kubernetes-local.md) | Pods, Services, Jobs, configuration and disposable node-local PVCs |
| 5. AWS foundation | [AWS k3s](aws-k3s.md), [acceptance record](aws-validation.md) | Terraform state, account, SSM access, EBS metadata, S3 artifacts, pause and teardown |
| 6. Helm and CI | [Helm](cicd-helm.md) | Chart values, adoption and image builds; direct SSM deployment is historical |
| 7. Model lifecycle | [Retraining](retraining.md) | Fixed-window training, evaluation gate, explicit approval, serving pin and rollback |
| 8. Monitoring | [Observability](observability.md) | Platform health, historical scores and checksum-matched observation verification |
| 9. GitOps | [Argo CD](gitops.md) | Reviewed Git desired state, manual sync, drift detection and Git-revert recovery |

The checked-in CI workflow publishes separate inference/training images and proposes image-release PRs. This describes repository behavior, not proof that live Argo adoption or CI cutover has completed. Check the Application before releasing. The GitOps guide's acceptance record lists outstanding live checks.

## 1. Select the target before operating

Local k3d (Docker engine must be running):

```sh
export KUBECONFIG="$PWD/.k3d/kubeconfig"
kubectl config current-context  # Expected: k3d-meteo-mlops
kubectl get nodes
```

AWS: choose the intended profile, inspect the account and Terraform-selected instance. Terraform must already be initialized with the correct local state.

```sh
export AWS_PROFILE=YOUR_DEMO_PROFILE
export AWS_DEFAULT_REGION="$(terraform -chdir=infra/terraform output -raw region)"
aws sts get-caller-identity
INSTANCE_ID=$(terraform -chdir=infra/terraform output -raw instance_id)
aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[].Instances[].{Id:InstanceId,State:State.Name}'
# Only if stopped:
scripts/aws-k3s.sh start
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
aws ssm describe-instance-information \
  --query 'InstanceInformationList[].{Id:InstanceId,Status:PingStatus}'
```

Wait for this instance to report SSM `Online`; instance-running alone is insufficient. If the private kubeconfig is missing, retrieve it with `uv run --locked python scripts/aws-kubeconfig.py`. In a dedicated terminal with the same AWS profile:

```sh
scripts/aws-k3s.sh tunnel
```

In each AWS operations terminal:

```sh
export KUBECONFIG="$PWD/.aws-k3s/kubeconfig"
kubectl config current-context
kubectl get nodes
kubectl wait --for=condition=Ready node/meteo-node --timeout=300s
```

The local context belongs to the local kubeconfig. Do not combine `--context k3d-meteo-mlops` with the AWS file. Port 6443 must be free for the AWS tunnel.

## 2. Inspect health and open private services

These commands use the target selected above:

```sh
kubectl -n meteo-mlops get deployments,pods,services,pvc,jobs,cronjobs
kubectl -n meteo-mlops rollout status deployment/inference --timeout=300s
kubectl -n meteo-mlops rollout status deployment/mlflow --timeout=300s
kubectl -n meteo-mlops get events --sort-by=.metadata.creationTimestamp
kubectl -n meteo-mlops logs deployment/inference --tail=100
kubectl -n meteo-mlops logs deployment/mlflow --tail=100
```

Run each access command in its own terminal with the selected kubeconfig:

| Service | Command | Browser address |
| --- | --- | --- |
| Inference | `kubectl -n meteo-mlops port-forward --address 127.0.0.1 service/inference 8000:8000` | http://127.0.0.1:8000/docs |
| MLflow | `kubectl -n meteo-mlops port-forward --address 127.0.0.1 service/mlflow 5000:5000` | http://127.0.0.1:5000 |
| Argo CD (AWS after installation) | `kubectl -n argocd port-forward --address 127.0.0.1 service/argocd-server 8080:443` | https://localhost:8080 |
| Grafana | `scripts/monitoring.sh aws grafana` (or `local grafana`) | http://127.0.0.1:3000 |
| Prometheus | `scripts/monitoring.sh aws prometheus` (or `local prometheus`) | http://127.0.0.1:9090 |

Argo/Grafana login instructions are in their guides. For inference:

```sh
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
curl -fsS http://127.0.0.1:8000/predict \
  -H 'Content-Type: application/json' \
  -d '{"forecasts":[{"valid_time":"2026-01-01T12:00:00Z","forecast_c":18.0}]}'
```

`/health` checks liveness. `/ready` checks the loaded model; record its full SHA-256 `model_version` and numeric `registry_version` when present. The example request checks API operation, not forecast accuracy. Reconnect port-forwards after pod replacement or node restart.

## 3. Train, inspect and approve a model

For workstation-only reproduction:

```sh
uv sync --locked
uv run --locked meteo-baseline run
uv run --locked meteo-tracking server
```

Without `MLFLOW_TRACKING_URI`, these use local `.mlflow` SQLite/artifacts. To operate the selected cluster's registry, keep its MLflow port-forward running and set:

```sh
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
kubectl -n meteo-mlops get jobs,cronjobs
JOB="training-$(date +%s)"
kubectl -n meteo-mlops create job "$JOB" --from=cronjob/training
kubectl -n meteo-mlops logs -f "job/$JOB" --pod-running-timeout=300s
kubectl -n meteo-mlops wait --for=condition=complete "job/$JOB" --timeout=1800s
```

Run one manual training Job at a time; CronJob `Forbid` does not serialize manually created Jobs. The Job uses the deployed CronJob image/config, not your local checkout. Check its configured command: the Helm lifecycle path uses `run --evaluate`; older raw manifests may only train/register. A schedule repeats its configured window unless `rolling_window: true` is enabled; see the pressure/rolling experiment in [retraining](retraining.md). Keep `training.suspend: true` until scheduling is intentional.

Find the FINISHED run in MLflow, record its Run ID/numeric model version, and review metrics, split dates, raw-baseline skill, feature contract and provenance. Then replace `7` with that version:

```sh
uv run --locked meteo-lifecycle evaluate 7
# After reviewing a passing decision:
uv run --locked meteo-lifecycle promote 7
uv run --locked meteo-lifecycle materialize 7 --output artifacts/release-7
shasum -a 256 artifacts/release-7/model.joblib
```

Promotion recomputes the gate and can reject the version. A passing gate still requires operator review. Materialization downloads a specific version; it does not approve or deploy it. Training, image publication and alias changes leave serving unchanged.

## 4. Release through the application owner

| Owner | Release method | Recovery method |
| --- | --- | --- |
| Raw lab manifests | Local/AWS deploy helper in its lab guide | Retained Deployment revision or recorded image/model redeploy |
| Manual Helm, before Argo adoption | Reviewed Helm values and `helm upgrade` | `helm history` and `helm rollback` |
| Argo CD after adoption | Reviewed Git values, then Argo refresh/diff/sync | Revert Git desired-state commit, then sync |

Use one owner for application resources. Raw `deploy` helpers reapply configuration and workloads; do not use them after Helm/Argo adoption. The separate monitoring Helm release can still be operated with `scripts/monitoring.sh`.

For an Argo application image release: CI passes → immutable images publish → image-release PR opens → review/merge → refresh → inspect diff → sync → verify. The two full Git-SHA tags select code/dependencies; the model version selects artifact bytes independently.

```sh
python3 scripts/argocd-demo.py refresh
python3 scripts/argocd-demo.py status
kubectl -n argocd get application meteo-mlops -o yaml
# Inspect the compared revision and resource diff in the Argo UI before syncing:
python3 scripts/argocd-demo.py sync
kubectl -n meteo-mlops get deployments,pods,pvc
```

If repository access reports `ComparisonError`, fix access before sync. Verify the Application's repository/path/targetRevision and the compared commit. The helper syncs the revision last compared by Argo; recheck status if the merged change has not appeared yet. Completion requires `Synced/Healthy`, successful rollouts, `/ready` identity and a prediction.

For a model release, edit `gitops/aws/values.yaml` in a separate reviewed commit: set `inference.registryVersion` to the approved numeric version. Preserve image tags. Review `inference.qualityConfigMap`; an old checksum-mismatched verification file becomes unavailable until refreshed. Commit, merge, refresh, review and sync as above. The init container downloads the exact registered bundle on pod startup. Keep the referenced MLflow metadata/artifacts available.

The alternative small-model Secret path uses `modelSecret` with an empty registryVersion. Provision the trusted immutable Secret before syncing its Git reference; Secrets are outside Argo ownership. Detailed creation/adoption procedures remain in the stage guides.

Record each release: Git desired-state commit, both image tags, model Run ID/version, full checksum, verification reference/window, previous release and readiness result.

## 5. Monitoring and diagnosis

```sh
scripts/monitoring.sh aws status  # Use local for k3d.
kubectl -n meteo-mlops top pods
kubectl -n argocd top pods       # AWS with Argo installed.
```

`top` requires metrics-server. In Grafana, check scrape health, pod restarts/resources, errors, latency and the served identity. Historical holdout scores do not establish current quality. Observation verification requires a trusted matched CSV, source/station/date provenance and the exact served artifact:

```sh
uv run --locked meteo-quality --model artifacts/release-7/model.joblib \
  --observations PATH_TO_MATCHED_OBSERVATIONS.csv \
  --output artifacts/verification/quality.json
```

Publish the checksum-matched verification ConfigMap using [the observability guide](observability.md). Set its reference through the application owner. There is no automatic live-observation refresh.

| Symptom | First check |
| --- | --- |
| Connection refused | Selected kubeconfig, AWS SSM Online, tunnel and port-forward terminals |
| Pending pod | `kubectl -n meteo-mlops describe pod POD_NAME`; PVC binding and node capacity |
| ImagePullBackOff | Published tag/repository, node IAM and outbound HTTPS; local `Never` policy requires image import |
| CrashLoopBackOff | `kubectl -n meteo-mlops logs POD_NAME --previous`; model format/checksum and memory |
| Failed training | Job logs/description and MLflow run; inspect before retrying |
| Argo OutOfSync | Reviewed Git-vs-live diff; manual mode detects drift until an operator syncs |
| Missing quality | Model checksum, ConfigMap projection, window validity and observation freshness |

## 6. Recover, pause and retain data

For an Argo application rollback, revert the release commit through a reviewed Git change, then refresh/sync and repeat API verification. For model rollback, also update the registry record when needed:

```sh
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
uv run --locked meteo-lifecycle rollback 6  # Previously approved, finished version.
```

Set the serving pin to that same version in Git (or restore the previous Secret reference), then sync. Alias rollback alone does not change inference. Application rollback restores references/configuration, not database or artifact contents.

Stop AWS compute after work:

```sh
scripts/aws-k3s.sh stop
aws ec2 wait instance-stopped --instance-ids "$INSTANCE_ID"
```

This stops workloads/controllers and releases the auto-assigned public IP; retained EBS, ECR and S3 storage still costs money. Stop port-forwards/tunnels with Ctrl-C. Restart with section 1. Suspend scheduled training in the owning values file when needed; stopping the node does not change its configured schedule.

Retain Terraform state, MLflow metadata plus artifacts, approved model bytes, immutable images and release records. Local cluster deletion destroys node-local storage. AWS destroy is a separate intentional teardown: follow [cost, backup and teardown](aws-k3s.md), including versioned S3 cleanup. Dated validation records are evidence, not backups or current inventory.

## Local verification before a change

```sh
uv run --locked ruff check .
uv run --locked pytest
helm lint charts/meteo-mlops --strict
helm lint charts/meteo-monitoring --strict
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test
```

Terraform validate/test require initialized providers; tests use mocks. Container smoke and live persistence exercises are in the relevant guides. Local checks do not prove hosted CI, OIDC access, successful cloud sync or a current observation-quality result.
