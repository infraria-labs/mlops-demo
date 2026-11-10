# Kubeflow Pipelines: isolated local learning lab

This optional lab uses a dedicated `meteo-kfp-lab` k3d cluster. It does not use
AWS credentials, AWS kubeconfig, ECR, the retained AWS MLflow registry, Argo CD
application or serving pin. The core architecture still uses Jobs/CronJobs and
MLflow; see [the comparison](platform-experiments.md).

## Prerequisites and isolation

Run from the repository root with Docker/OrbStack running, `k3d`, `kubectl`, Git,
Python and `uv` installed. The verified host has an ARM CPU and 8 GiB allocated
to Docker. Images require internet downloads on first setup. This is a private,
single-user, disposable learning environment, not a production installation.

The helper always supplies `k3d-meteo-kfp-lab` and `.kfp-lab/kubeconfig`; cluster
creation does not update or switch the default kubeconfig. Use the helper or
both explicit flags for every manual command:

```sh
kubectl --kubeconfig "$PWD/.kfp-lab/kubeconfig" \
  --context k3d-meteo-kfp-lab -n kubeflow get pods
```

All MLflow state is in the lab's `lab-mlflow-data` PVC. KFP additionally uses
MySQL and SeaweedFS PVCs for its own metadata and artifacts. Cluster deletion
removes this node-local storage. No model is promoted or served by this pipeline.

## Create, install and compile

```sh
bash experiments/kubeflow/lab.sh create
bash experiments/kubeflow/lab.sh sdk
bash experiments/kubeflow/lab.sh preload
bash experiments/kubeflow/lab.sh install
bash experiments/kubeflow/lab.sh build
bash experiments/kubeflow/lab.sh compile
bash experiments/kubeflow/lab.sh status
```

KFP backend and SDK: **2.17.0**. Argo Workflows dependency: **v4.0.5**. k3s:
**v1.33.9-k3s1**. The install uses the release `platform-agnostic` overlay;
the `dev` overlay selects mutable `master` images. Unused viewer/visualization, legacy v1 cache/metadata writer and recurring-run
services are scaled to zero. This lab supports v2 manual runs only; it does not
exercise those disabled features. `preload` pulls through Docker and exports the
loaded architecture explicitly before importing into k3d; this avoids stalled
node downloads and incomplete multi-platform archive errors. `render.py` replaces remote
Argo Kustomize references with a checkout of the same pinned release to avoid
repeated Git fetch timeouts. Upstream installation reference:
[standalone KFP](https://www.kubeflow.org/docs/components/pipelines/operator-guides/installation/).

`build` copies `data/raw` into a temporary Docker build context. These files are
ignored by Git and must exist before building. For a fresh checkout, follow
[baseline retrieval and provenance](baseline.md); fetching today can yield
revised observations, so compare only identical snapshot hashes. The pipeline
uses `train`, not `run`, and makes no weather archive requests. The packaged
configuration defines chronological train/validation/test splits. Inputs are
NZWN METAR `tmpc` and GFS `temperature_2m_previous_day1`, fixed 24-hour horizon.

The snapshot and worker are baked into `meteo-kfp-lab:v1`, based on the existing
locked training target. Rebuild/import after changing either; keep this local
lab tag separate from immutable AWS release tags. The isolated SDK lives in
`.kfp-lab/sdk`; core `pyproject.toml` and `uv.lock` are unchanged.

For snapshot/worker-only changes, `bash experiments/kubeflow/lab.sh build --reuse-training`
reuses the existing training image. Rebuild the training target normally whenever
core source or dependencies change.

## Run and inspect

Keep each port-forward in a separate terminal:

```sh
bash experiments/kubeflow/lab.sh api
bash experiments/kubeflow/lab.sh ui
bash experiments/kubeflow/lab.sh mlflow
```

Open KFP at <http://127.0.0.1:8081> and lab MLflow at <http://127.0.0.1:5001>.
Then submit:

```sh
.kfp-lab/sdk/bin/python experiments/kubeflow/run.py
.kfp-lab/sdk/bin/python experiments/kubeflow/run.py --fail-evaluation
```

The graph is `train → evaluate`. Training runs the existing CLI and emits its
numeric registry version. Evaluation consumes that parameter and downloads the
bundle from the same lab registry, records the lifecycle decision and returns
JSON. An eligible decision does not promote the model. A rejected quality gate
is a valid evaluation result; `--fail-evaluation` instead deliberately raises a
runtime error after recording the decision, demonstrating a failed task. Evaluation allows one retry, so the failure
exercise executes evaluation twice without rerunning training.

Find the experiment `isolated-meteo-lab` in KFP. Open a run to inspect the graph,
step logs and version/decision output parameters. In MLflow, inspect the run,
source/data hashes, selected model, numeric version and `lifecycle/decision.json`.
`run.py` exports backend run metadata to `artifacts/kfp-lab/<run-id>.json`.

Caching is disabled for both tasks: training registers a version, and evaluation
reads a mutable champion reference. Retrying training can create another version.
To learn caching safely, add a separate pure preprocessing component keyed by
snapshot/config/image hashes; do not enable caching around registry side effects.
See [KFP caching](https://www.kubeflow.org/docs/components/pipelines/user-guides/core-functions/caching/).

## Compare and troubleshoot

For a direct Kubernetes Job using the same image and registry:

```sh
kubectl --kubeconfig "$PWD/.kfp-lab/kubeconfig" --context k3d-meteo-kfp-lab \
  apply -f experiments/kubeflow/job.yaml
kubectl --kubeconfig "$PWD/.kfp-lab/kubeconfig" --context k3d-meteo-kfp-lab \
  -n kubeflow wait --for=condition=complete job/meteo-job-reference --timeout=300s
kubectl --kubeconfig "$PWD/.kfp-lab/kubeconfig" --context k3d-meteo-kfp-lab \
  -n kubeflow logs job/meteo-job-reference
```

Delete only this completed Job before submitting it again. It registers another
lab version and evaluates that version, but does not promote it.

Generate a workstation reference in its own registry (core dependencies must
be installed with `uv sync --locked` first):

```sh
mkdir -p artifacts/kfp-lab/reference
MLFLOW_TRACKING_URI="sqlite:///$PWD/artifacts/kfp-lab/reference/mlflow.db" \
MLFLOW_ARTIFACT_ROOT="file://$PWD/artifacts/kfp-lab/reference/mlflow-artifacts" \
  uv run --locked meteo-baseline train --data data/raw \
    --output artifacts/kfp-lab/reference/bundle
```

With the lab MLflow forward running, compare the actual numeric versions shown
in your lab (these numbers are examples, not AWS registry versions):

```sh
uv run --locked python experiments/kubeflow/verify.py 1 2
```

The verifier checks identical snapshot/config/lock hashes, selected method,
counts and all split metrics within an absolute tolerance of `1e-8`, and requires
empty lab model aliases. Record selected method, split metrics, data/config/lock
hashes, cold setup time, warm execution duration and resource usage. Pipeline
submission adds a graph and stage boundary; it does not change scientific scoring.

If startup is slow, inspect Pods/events and PVCs: initial image downloads can
hold database/storage helper Pods pending. Legacy cache services are disabled here; a terminating old cache Pod can
remain briefly after the initial installation is reduced. Check architecture if metadata fails:
the upstream ML Metadata 1.14 image is amd64, unlike the multi-architecture KFP
images. `preload` imports that amd64 image explicitly; the verified OrbStack
host executes it through emulation. Do not replace it with an arbitrary version; backend compatibility matters.

```sh
kubectl --kubeconfig "$PWD/.kfp-lab/kubeconfig" --context k3d-meteo-kfp-lab \
  -n kubeflow get events --field-selector type=Warning
kubectl --kubeconfig "$PWD/.kfp-lab/kubeconfig" --context k3d-meteo-kfp-lab \
  -n kubeflow logs deployment/lab-mlflow
```

## Pause and cleanup

Stop port-forwards with Ctrl-C. Retain/export evidence before deleting storage.
Workflow metadata expires after one hour in this lab; export it while available.
There are no recurring cloud charges from this lab; it consumes local CPU, memory
and disk while running. Stopping retains its Docker volumes:

```sh
bash experiments/kubeflow/lab.sh stop
bash experiments/kubeflow/lab.sh start
```

To discard the lab completely:

```sh
bash experiments/kubeflow/lab.sh destroy
k3d cluster list
```

Verify `meteo-kfp-lab` is absent. Local lab images, downloaded checkouts, SDK and
exported evidence remain; remove those separately when no longer needed. Do not
run a global Docker prune or delete another cluster to clean this lab.

## Local acceptance record — 2026-10-08 (Pacific/Tahiti)

Validated on ARM OrbStack with 8 GiB Docker memory, using only the named local
cluster. AWS was not queried, resumed, deployed to or changed.

| Execution | KFP run ID / lab registry version | Result / elapsed time |
| --- | --- | --- |
| First KFP run | `70331ae9-53e7-4c8b-84c4-2b4b872e86a3` / 2 | Succeeded, 504 seconds including execution-image downloads |
| Warm KFP run | `dced3b6b-efa4-4909-a3b0-5248e06378bd` / 3 | Both stages succeeded, 72 seconds |
| Initial failure test | `1939d1ff-b4c2-4a0c-a871-1ad91b7925b0` / 4 | Failed as intended; exposed inherited `OnError` retry policy |
| Corrected retry test | `ac9c5a55-95e8-49d1-bc91-054e5aa8e34f` / 6 | Failed as intended, 92 seconds; two evaluation attempts, one training invocation |
| Equivalent plain Job | MLflow run `aecb766734a64ddf9816d25ec8c58f57` / 5 | Training and evaluation succeeded, 17 seconds |

The earlier training-only reference Job created version 1 in 15 seconds. All six
lab versions matched the workstation reference's snapshot/config/lock hashes,
selected method, split counts and RMSE/MAE/bias within `1e-8`. The lab registry
retained **no model aliases**. The selected method was mean-bias correction;
on 4,416 held-out hours from 2025-07-01 through 2025-12-31 UTC, its RMSE was
1.200024 °C versus raw forecast RMSE 1.458066 °C. These reproduce existing data,
not a new prospective weather-quality assessment. Observation provenance:
NZWN, [IEM routine METAR `tmpc`](https://mesonet.agron.iastate.edu/request/download.phtml);
forecast provenance: [Open-Meteo Previous Runs](https://open-meteo.com/en/docs/previous-runs-api),
`gfs_global` / `temperature_2m_previous_day1`. Inputs span 2024–2025.

The KFP UI was checked directly: its graph shows `train → evaluate`, with
version `2` as evaluation input and the eligible decision JSON as output. KFP
persists task logs to SeaweedFS; MLflow remains the model-bundle artifact store.
These components pass small parameters, not KFP `Model` artifacts.

One near-idle resource snapshot measured roughly **758 MiB for eight KFP services**
and **267 MiB for the separate MLflow service**, excluding Kubernetes/system
processes. This is one local observation, not a capacity guarantee or an AWS
benchmark. The timing comparison is also one warm pair; KFP creates graph and
component-driver Pods in addition to the two execution Pods.

Important setup findings:

- The locked training image's first dependency download/build took about 17
  minutes on this connection. Cold platform setup also needed image preloading
  and troubleshooting; the 72-second warm run does not represent setup effort.
- ARM KFP images work, but the upstream MLMD image required amd64 emulation.
- Exporting Docker images with an explicit platform avoided incomplete manifest
  import failures. Preloading **before** installing workloads avoids stale node
  pulls; replacing the never-started SeaweedFS Pod cleared one stale pull.
- The pinned [controller default](https://github.com/kubeflow/pipelines/blob/2.17.0/manifests/kustomize/third-party/argo/base/workflow-controller-configmap-patch.yaml)
  sets `OnError`, which did not retry the worker's ordinary exit-code-1 failure.
  The lab's `retry-policy.json` uses `Always` with zero default retries; the
  evaluation component explicitly requests one retry. This is a lab-only
  override. [Argo retry semantics](https://argoproj.github.io/argo-workflows/retries/)
  distinguish failed tasks from controller errors.
- Task caching remains disabled and was checked in the compiled IR. Cache hits
  were not exercised; a pure preprocessing component is a separate follow-up.

Run metadata, image identity, downloaded bundles, comparison JSON, Job/workflow
records and retry logs are retained under ignored `artifacts/kfp-lab/`. The
cluster remains running for exploration, with localhost UI/API/MLflow forwards;
use the pause/cleanup commands above when finished. No serving Deployment exists
in this lab, and no promotion or deployment step was executed.
