# Observability

Purpose: inspect platform health and verify the exact served model against observations. Prerequisites: a deployed application image exposing `/metrics`, Helm 3 and access to the target cluster. Monitoring remains a separate Helm release; application changes use [GitOps](gitops.md) after adoption.

`charts/meteo-monitoring` is a separate, dependency-free Helm release for local
k3d and AWS k3s. It contains Prometheus, Grafana and kube-state-metrics. It avoids
an Operator, Alertmanager, extra CRDs and node-exporter DaemonSets on the small
single-node demonstrator. One monitoring release per application namespace is
supported. Use Helm 3, as in the application deployment guide. The application
and monitoring releases can be upgraded independently.

## Instrumentation — the implementation

Inference exposes `/metrics` in Prometheus text format. Scrapes are excluded
from request metrics. Each inference replica has its own counters; Prometheus
uses pod discovery to scrape all replicas, avoiding Service load-balancing gaps.

| Metric | Meaning |
| --- | --- |
| `meteo_requests_total{route,status}` | HTTP responses including 422 validation and 503 readiness failures |
| `meteo_errors_total{route,status}` | HTTP responses with status >=400 |
| `meteo_request_duration_seconds{route}` | End-to-end request histogram, including validation |
| `meteo_predictions_total` | Successfully corrected forecast rows, distinct from batch request count |
| `meteo_model_info{version,registry_version,method}` | Exact loaded SHA-256, pinned registry version or `unregistered`, correction method |
| `meteo_model_loaded_timestamp_seconds` | Process load time; this is not model training age |
| `meteo_model_trained_timestamp_seconds` | Actual training timestamp for newly trained bundles; NaN for legacy bundles |
| `meteo_holdout_quality{method,metric}` | Raw and served correction RMSE, bias, skill and final-168-row bias on the historical holdout |
| `meteo_verification_quality{method,metric}` | Explicit matched-observation evaluation for the checksum-matched served artifact |
| `meteo_verification_available` | 1 only when the mounted verification file is valid for the loaded model |
| `meteo_verification_end_timestamp_seconds` | Latest matched observation time; exposes verification staleness |
| `meteo_verification_samples` | Number of matched rows in the supplied observation window |

Route labels are restricted to `/health`, `/ready`, `/predict`, and `other`.
No request IDs, timestamps, locations, URLs, or model versions appear on request
counters/histograms. The model checksum appears only on the single info series
per process. Restarting a process resets counters; use `rate()` and `increase()`.
Run one Uvicorn worker per pod (the Docker default); scale pods rather than
adding workers because these collectors are process-local.

Kube-state-metrics reads only pods, deployments, replicasets, jobs and CronJobs
in the application namespace. Prometheus scrapes cAdvisor through the Kubernetes
API for pod CPU and memory, retaining only this namespace and those two metric
families. This requires cluster-scoped node discovery and `get` on `nodes/proxy`;
that permission grants broader kubelet proxy access and is restricted to the
Prometheus service account. Application/Grafana pods receive no such permission.
Job status remains visible while the Job object exists, so TTL deletion also
removes its status metric. It is not a durable training audit log; MLflow is.

## Deployment — the implementation

Build and deploy the updated inference image through [GitOps](gitops.md) after
Argo adoption, or the [Helm workflow](cicd-helm.md) in an unmanaged lab. Old inference images do not implement `/metrics`.
The monitoring chart does not upgrade the application or promote any model.

For the local cluster:

```sh
scripts/k3d-local.sh create # only if the lab cluster does not already exist
scripts/monitoring.sh local deploy
scripts/monitoring.sh local status
scripts/monitoring.sh local grafana
```

For AWS, start the demo node if intentionally stopped, verify the account and
instance as described in [the AWS lab](aws-k3s.md), and leave the private tunnel
running in another terminal:

```sh
scripts/aws-k3s.sh tunnel
scripts/monitoring.sh aws deploy
scripts/monitoring.sh aws status
scripts/monitoring.sh aws grafana
```

Both targets use their explicit repository kubeconfig. Services are ClusterIP;
there is no Ingress, NodePort, LoadBalancer or public security-group change.
Port-forwards bind only to localhost. Open `http://localhost:3000`, user `admin`.
Retrieve the generated password locally, without putting it in version control:

```sh
kubectl --kubeconfig "$PWD/.k3d/kubeconfig" --context k3d-meteo-mlops \
  -n meteo-mlops get secret monitoring-grafana -o jsonpath='{.data.password}' | base64 --decode
```

For AWS, the node must be running and `scripts/aws-k3s.sh tunnel` must remain
open in another terminal. Retrieve its separate Grafana password with:

```sh
kubectl --kubeconfig /path/to/mlops-demo/.aws-k3s/kubeconfig \
  -n meteo-mlops get secret monitoring-grafana \
  -o jsonpath='{.data.password}' | base64 --decode
```

Omit `--context` to use the AWS kubeconfig's configured context. The
`k3d-meteo-mlops` context belongs to the local kubeconfig; using it with the AWS
kubeconfig produces `context "k3d-meteo-mlops" does not exist`.

Anonymous Grafana access and signup are disabled. Prometheus has no built-in user login;
its UI is reachable only through authenticated Kubernetes port-forward access
(or trusted workloads inside the cluster). This is adequate for this private
demo, not a multi-tenant monitoring platform.

| Component | CPU request / limit | Memory request / limit |
| --- | --- | --- |
| Prometheus | 100m / 500m | 192Mi / 512Mi |
| Grafana | 50m / 500m | 128Mi / 256Mi |
| kube-state-metrics | 20m / 500m | 32Mi / 128Mi |

Total requests are 170m CPU / 352Mi memory; memory limits total 896Mi. Training
and MLflow also need headroom on the 4GiB AWS node. Check `kubectl top pods` and
restarts after representative training/load, and adjust chart values if needed.
Scrapes run every 30 seconds with sample limits. Prometheus retains 48 hours,
caps retained blocks at 512MB, and has a 1Gi local-path PVC (WAL/head blocks need
additional space). This PVC survives pod restarts and Helm uninstall, but local
node/cluster destruction loses it. Grafana is ephemeral: provisioning restores
the datasource/dashboard after restart; UI preferences and sessions are disposable.
No extra AWS service or volume is provisioned; monitoring uses existing node
compute/storage. A running EC2 node continues to cost money. Stop it through
`scripts/aws-k3s.sh stop` when finished; do not pause only monitoring to claim
cloud cost has stopped.

To upgrade, re-run `deploy`. Failed upgrades roll back automatically. Inspect
Helm status, pods and events; for a manual rollback use `helm rollback monitoring REVISION --wait` using
the target kubeconfig. To remove the stack, run `scripts/monitoring.sh TARGET remove`.
The Prometheus PVC and Grafana Secret remain intentionally. Delete those two
objects explicitly only when their history/password is no longer needed. Namespace
or cluster deletion also deletes local storage. Do not remove the application
release to remove monitoring.

## Dashboard and observation verification — the implementation

Grafana provisions the **Meteo MLOps — platform and model health** dashboard at
`/d/meteo-mlops`. Its 22 panels show scrape health, exact production identity and
training age, prediction volume, request/error rate and p95 latency, pod
readiness/restarts, CPU/memory, raw/corrected historical RMSE and improvement,
bias, training job state and explicit observation verification/freshness.
No traffic means no rate/latency samples yet. Missing quality means unavailable,
not a score of zero. Legacy models retain their original checksum and need
explicit verification rather than fabricated training dates or another model's
scores. The served model can differ from MLflow's champion until explicitly deployed.

New training bundles embed historical holdout scores. The dashboard labels these
as historical; its final-168-hour bias is a trailing holdout statistic. For a
rolling observation window, prepare a trusted matched CSV with `valid_time`
(UTC), `forecast_c` (GFS global temperature, 24h horizon), and `observed_c` (NZWN
temperature in C). Preserve station/source URLs, raw fields, extraction dates and
matching method alongside the CSV; see [baseline provenance](baseline.md).
The verification command rejects insufficient/nonfinite data, duplicate times,
subhourly rows, and windows overlapping training/selection plus the forecast
matching embargo. It recomputes predictions with the exact trusted model:

```sh
uv run --locked meteo-quality --model artifacts/baseline/model.joblib \
  --observations artifacts/baseline/test-predictions.csv --output artifacts/verification/quality.json
kubectl --kubeconfig "$PWD/.k3d/kubeconfig" --context k3d-meteo-mlops -n meteo-mlops \
  create configmap inference-verification --from-file=quality.json=artifacts/verification/quality.json \
  --dry-run=client -o yaml | kubectl --kubeconfig "$PWD/.k3d/kubeconfig" --context k3d-meteo-mlops -n meteo-mlops apply -f -
```

In the application Helm values, set `inference.qualityConfigMap: inference-verification`
and deploy through the application owner (Git commit and Argo sync after adoption;
Helm upgrade in an unmanaged lab), preserving the current image/model values. The inference container
mounts `/verification`, reads `QUALITY_PATH` on every scrape, and rejects a
checksum mismatch. CI image releases preserve the current verification ConfigMap
reference; a changed model checksum disables the old scores. ConfigMap updates propagate without a process restart
(after Kubernetes' normal projection delay). A model rollout invalidates stale
verification automatically. Use the AWS kubeconfig for the cloud equivalent.

Supply an updated CSV/window and rerun verification to update rolling metrics.
The baseline CSV demonstrates historical verification, not current production
quality. The command does not ingest live observations, infer station/horizon
provenance, schedule refreshes, or promote models. Rolling bias uses the latest
168 matched hourly rows; gaps can make that span longer than seven days. Bias is
prediction minus observation; skill is `1 - corrected RMSE / raw RMSE`, with
undefined skill omitted when raw RMSE is zero.

## Validation

```sh
uv run --locked ruff check .
uv run --locked pytest
helm lint charts/meteo-monitoring --strict
scripts/monitoring.sh local prometheus
```

In Prometheus check `/api/v1/targets` and query `up`, `meteo_model_info`,
`meteo_predictions_total`, `kube_pod_status_ready`, and
`container_memory_working_set_bytes`. Generate valid and invalid prediction
requests, wait two scrape intervals, and verify the error and latency panels.
Check Grafana `/api/health` and the provisioned dashboard. Restart Prometheus to
verify PVC retention and Grafana to verify provisioning restoration.

Configuration references: [Prometheus Kubernetes discovery](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
and [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).

The smoke check can verify every provisioned PromQL expression and the authenticated
Grafana datasource, keeping the admin credential inside its pod (run each port-forward in a separate terminal first):

```sh
uv run --locked python scripts/monitoring_smoke.py \
  --kubeconfig "$PWD/.k3d/kubeconfig" --context k3d-meteo-mlops
```

## Implementation validation on 2026-10-06

Ruff, all 60 pytest checks, both Helm lints, and the final ARM64 inference
container smoke passed. A fresh local k3d installation and upgrade passed;
all four Prometheus scrape jobs were healthy, all 22 dashboard expressions
were accepted, and Grafana's provisioned dashboard and datasource were healthy.
Real prediction traffic verified row counts, validation errors and a finite
p95 latency. Prometheus retained samples across pod restarts; Grafana restored
provisioning after restart.

AWS monitoring installed successfully on the existing t4g.medium k3s node.
At idle its three pods used approximately 113MiB combined (24MiB Prometheus,
79MiB Grafana, 10MiB kube-state-metrics); this is not a sustained-load benchmark.
Platform scrapes and existing completed training Jobs were visible, Prometheus
history survived a Helm restart, and every dashboard expression was accepted.
The unchanged serving model was evaluated inside its pod against only the
public matched NZWN METAR/GFS fields; no model bytes were exported.

The verification demonstration uses 4,416 matched hours from July–December
2025, with a trailing 168-row bias. It does not establish October 2026 quality.
The historical AWS bundle has no training timestamp, so its training age is
unavailable. New training bundles record it. A fresh observation window must
be supplied explicitly to update rolling verification.

The final ARM64 inference image was published to the existing ECR repository as
`inf74-20261006-v1` (manifest digest
`sha256:00f2f3d66cbaf9e1bc913f65fbf7d0977f271845ae17af4ab054e18bc00c69e0`).
AWS application Helm revision 6 rolled out successfully while preserving model
checksum `249d1d104e0ed3a7a58ade890d7ff7d70c0810265dbced7e1db544cf140f3cad`.
All four AWS scrape jobs were healthy and the authenticated Grafana datasource
and provisioned dashboard checks passed. Prometheus recorded 14 prediction rows,
one validation error and a finite p95 latency (about 10ms for this small demo
traffic sample), plus checksum-matched raw/corrected RMSE and trailing bias.
The temporary AWS validation run is stopped after verification; monitoring
remains installed and will resume when the node starts again. Application images
and monitoring charts were validated manually here; hosted CI execution is a
separate release check.
