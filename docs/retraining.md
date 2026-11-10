# Trained-model lifecycle: experiment, approve, deploy and retrain

This guide follows a model from a custom experiment to serving and rollback.
Run workstation commands from the repository root. Cluster commands require the
explicit kubeconfig and, for AWS, the SSM tunnel in [operations](operations.md#1-select-the-target-before-operating).
Examples use model versions `7` and `6`; replace them with versions in the
**selected tracking store**. These are examples, not release recommendations.

## Quick workflow: local experiment to AWS deployment

Use this checklist for a new experiment. The sections below explain each step
in more detail. Run from the repository root with `uv sync --locked` completed.

1. **Build the experiment on a branch.** Commit its JSON configuration, feature
   construction, required input data, dependencies and tests. Training and
   inference must construct the same features; keep the previous champion
   compatible with evaluation and retain a compatible image/model pair for
   rollback. `experiments/pressure-rolling.json` is the pressure/rolling example.
   Rolling splits preserve the template durations and gaps, ending the test
   period exclusively at 00:00 UTC on the training date. Inspect observation
   availability and coverage rather than assuming every requested hour exists.

2. **Train and evaluate locally.** Local MLflow stores metadata in
   `.mlflow/mlflow.db` and artifacts in `.mlflow/artifacts`. Each registration
   creates a new numeric version. Select an explicit date to reproduce a run;
   omit `--training-date` to resolve today's UTC date automatically.

   ```sh
   unset MLFLOW_TRACKING_URI
   uv run --locked meteo-baseline run \
     --config experiments/pressure-rolling.json \
     --training-date 2026-10-09 \
     --data data/experiments/pressure-rolling \
     --output artifacts/experiments/pressure-rolling \
     --evaluate
   ```

   Inspect the configuration-hash output directory's `tracking.json`,
   `metadata.json`, `evaluation.md` and MLflow `lifecycle/decision.json`.
   Validation selects the exported model; test results describe the holdout.
   Local promotion is optional for local testing and does not approve AWS:
   `uv run --locked meteo-lifecycle promote LOCAL_VERSION`.

3. **Register and evaluate in AWS MLflow.** Start the AWS SSM tunnel as described
   in [operations](operations.md#1-select-the-target-before-operating). Stop any
   workstation MLflow server on port 5000, then keep this forward open:

   ```sh
   export KUBECONFIG="$PWD/.aws-k3s/kubeconfig"
   kubectl -n meteo-mlops port-forward --address 127.0.0.1 service/mlflow 5000:5000
   ```

   In the experiment terminal, set
   `export MLFLOW_TRACKING_URI=http://127.0.0.1:5000`, then repeat the training
   command with the same date/config/data but a separate output directory,
   such as `artifacts/experiments/pressure-rolling-aws`. Compute stays local;
   the new run, artifacts and version belong to AWS MLflow. Read its
   `tracking.json`: local and AWS version numbers are independent.
   The gate compares against the AWS champion on the candidate's held-out rows.

4. **Approve the AWS candidate explicitly.** Review the gate decision and
   provenance, then, with the AWS tracking URI still set, run
   `uv run --locked meteo-lifecycle promote AWS_VERSION`. Replace the placeholder
   with the actual AWS number. Promotion reevaluates the gate, records approval
   and updates `previous`/`champion`; it does not change serving. RMSE must
   improve by at least 0.02°C with no increase in MAE or absolute bias.

5. **Release compatible application images.** Push the code/configuration PR,
   pass CI and merge. Main CI publishes immutable training/inference images
   and proposes an image-release PR. Review and merge that PR; verify the
   chosen image tags contain the required feature and window logic.
   If code/dependencies are unchanged, reuse an already compatible release.

6. **Release the model pin in Git.** In a separate model-release PR, set
   `inference.registryVersion: "AWS_VERSION"` in `gitops/aws/values.yaml`,
   preserving the compatible image tags. Update prediction clients to provide
   any new required inputs, such as `pressure_msl_hpa`. Review the existing
   quality ConfigMap: old checksum-mismatched scores become unavailable until
   verification is refreshed. Merge, refresh Argo, inspect the compared Git
   revision/resource diff, then sync using its UI or:

   ```sh
   export KUBECONFIG="$PWD/.aws-k3s/kubeconfig"
   python3 scripts/argocd-demo.py refresh
   python3 scripts/argocd-demo.py status
   # Inspect the compared revision and diff before syncing.
   python3 scripts/argocd-demo.py sync
   kubectl -n meteo-mlops rollout status deployment/inference --timeout=300s
   ```

7. **Verify the deployment.** Require Argo `Synced/Healthy`, completed rollout,
   `/ready` reporting the approved numeric version and the expected artifact
   SHA-256, and a successful prediction with the required inputs. A rollout
   waits for the replacement pods to become available; it does not verify
   forecast accuracy. Refresh matched-observation verification for the new
   checksum. For rollback, restore the previous compatible image/model pin in
   Git and sync; update the registry alias separately when appropriate.

8. **Configure recurring training separately.** Copy the complete experiment
   JSON into `training.config` in the AWS GitOps values. Set
   `training.schedule: "0 3 * * 0"` and `training.suspend: false`, review/merge
   and sync. This runs Sunday 03:00 UTC / Saturday 17:00 Tahiti, with the AWS node
   running. Confirm the live `training-config` has pressure features and
   `rolling_window: true`; run one manual Job from the updated CronJob and
   inspect its logs before relying on scheduling. Manual Jobs are not serialized
   by CronJob `Forbid`. Candidates are registered/evaluated automatically;
   promotion, model-pin PRs and deployment remain manual. Pause by committing
   `training.suspend: true` and syncing.

**If a Job fails:** read its pod logs, not just `BackoffLimitExceeded` (zero
retries are configured). An old fixed test window may overlap the current
champion's training/validation embargo; update the deployed training config and
launch a new Job. Training may already have registered a version before
lifecycle evaluation fails. A completed Job with `approved: false` is a valid
rejected candidate, not a platform failure.

## 1. Lifecycle and naming conventions

| State | What it means | What happens next |
| --- | --- | --- |
| Experiment | A configuration defines data splits and candidate hyperparameters | Run training locally or as a Kubernetes Job |
| Registered | A finished run has a numeric version containing its validation-selected bundle | Inspect evidence and evaluate against champion, or raw forecasts for the first champion |
| Eligible | Lifecycle evaluation passes the comparison gate | Operator reviews and explicitly runs promotion |
| Champion | Promotion records approval and moves the registry alias | Operator separately changes the serving pin and deploys |
| Serving | Inference has loaded the pinned artifact | Verify readiness, identity, predictions and observation quality |
| Previous | Promotion saved the old champion alias | Retain its artifacts and use an explicit rollback when needed |

Training → registration → evaluation → operator promotion → serving-pin change
→ deployment → verification. A rejected or failed experiment leaves champion
and serving unchanged. A champion can differ from the served version until the
operator deploys it.

| Identifier | Convention and purpose |
| --- | --- |
| Experiment name | `meteo/NZWN/gfs_global/temperature_2m/24h`; parameter variants share this experiment |
| Registered model | `nzwn-gfs-temperature-24h`; versions contain artifact bundles, not MLflow pyfunc models |
| Run ID | Identifies a training invocation and its configuration, provenance, metrics and artifacts |
| Numeric model version | Identifies a registered bundle in one tracking store; local version 7 and AWS version 7 can be different models |
| `candidate` alias | Optional mutable pointer for review; training does not assign it automatically |
| `champion` alias | Mutable approved-model pointer; promotion uses the gate |
| `previous` alias | Previous champion saved during promotion; not necessarily the previous serving release |
| Image tag | Code/dependencies, independent of model selection; CI uses immutable full Git-SHA tags |
| `model_version` in API | Full SHA-256 of loaded `model.joblib`, despite the field name |
| `registry_version` in API | Numeric serving version when accompanied by `registry.json`; absent for the legacy Secret path |

Suggested local experiment names: `ridge-xgb-v1`, `ridge-xgb-v2`, with separate
configuration files, cache directories and output directories. These names are
operator conventions, not new CLI flags. Commit the configuration and source
needed to reproduce a release. Record the tracking store, Run ID, registry
version, configuration/data hashes, image tags, artifact checksum and serving
Git commit together.

## 2. Choose the tracking store first

For a workstation experiment using local SQLite and local artifacts:

```sh
uv sync --locked
unset MLFLOW_TRACKING_URI
uv run --locked meteo-tracking server
```

Keep the UI terminal open at http://127.0.0.1:5000. In the experiment terminal,
also unset `MLFLOW_TRACKING_URI`. Training can write directly to `.mlflow` without
the server running. Back up `.mlflow/mlflow.db` and `.mlflow/artifacts` together.

For the AWS registry, keep the SSM tunnel running, select the AWS kubeconfig,
then keep this port-forward open in another terminal:

```sh
export KUBECONFIG="$PWD/.aws-k3s/kubeconfig"
kubectl -n meteo-mlops port-forward --address 127.0.0.1 service/mlflow 5000:5000
```

In every workstation terminal that should use that registry:

```sh
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
```

Stop a workstation MLflow server occupying port 5000 first. Confirm which UI
is open before training or promoting. A locally executed experiment can log to
the AWS registry through this endpoint; its compute remains on your workstation.
AWS Jobs use `http://mlflow:5000` inside the cluster, with artifact bytes proxied
to S3 and metadata retained on EBS.

A local-only registry version cannot be deployed by an AWS init container. To
use that experiment on AWS, run the chosen configuration against the AWS tracking
endpoint (locally or in-cluster), then evaluate the newly registered AWS version.
This creates a new run/version; it does not migrate the original identifiers.

## 3. Define specific model parameters

Hyperparameters go in JSON supplied with `--config`; there are no CLI flags
such as `--max-depth` or `--model xgboost`. Start from the packaged configuration:

```sh
mkdir -p experiments
cp src/meteo_mlops/config.json experiments/ridge-xgb-v1.json
```

Edit that file. A complete supported example is:

```json
{
  "ridge_alphas": [0.1, 1.0, 10.0],
  "seed": 45,
  "xgboost_candidates": [
    {"n_estimators": 150, "max_depth": 2, "learning_rate": 0.03, "min_child_weight": 20},
    {"n_estimators": 250, "max_depth": 3, "learning_rate": 0.03, "min_child_weight": 30}
  ],
  "splits": {
    "train": ["2024-01-01", "2024-12-30"],
    "validation": ["2025-01-01", "2025-06-29"],
    "test": ["2025-07-01", "2026-01-01"]
  },
  "minimum_split_hours": 1000
}
```

| Setting | Effect |
| --- | --- |
| `ridge_alphas` | Positive regularization candidates; validation RMSE chooses the ridge alpha |
| `xgboost_candidates` | Positive values for exactly the four shown parameters; validation RMSE chooses the XGBoost configuration |
| `seed` | Integer seed; XGBoost also uses one CPU thread and full row/column sampling |
| `splits` | UTC start inclusive/end exclusive; ordered train/validation/test with at least 24h10m between splits |
| `minimum_split_hours` | Minimum paired rows required per split; do not lower it just to force a weak experiment through |

Every run compares **raw forecast, training mean-bias correction, ridge and
XGBoost**. Validation RMSE selects the exported method across all four. One
XGBoost candidate only fixes its search grid; it does not force XGBoost to win.
There is no configuration option to train only one family or override selection.
The test split reports held-out performance and does not choose the winner.

The station, source and horizon remain NZWN/GFS/24h. Data are
Open-Meteo `temperature_2m_previous_day1` and independent IEM METAR `tmpc` in °C;
source URLs and hashes are saved. See [baseline design](baseline.md) before
changing splits. The example reproduces the historical window; repeated tuning
after inspecting its test results does not provide a fresh generalization test.
Choose new supported archive dates deliberately for a new holdout; windows remain fixed unless `rolling_window: true` is enabled.

Validate the file without fetching or training:

```sh
uv run --locked python - <<'PY'
from pathlib import Path
from meteo_mlops.config import load_config
print(load_config(Path("experiments/ridge-xgb-v1.json")))
PY
```

### Sea level pressure and rolling windows

Use `experiments/pressure-rolling.json` for the pressure experiment. It adds
`pressure_msl_hpa` to the existing five features and enables `rolling_window`.
Pressure is GFS `pressure_msl_previous_day1` in hPa from the Open-Meteo
[Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api), matching
temperature's 24-hour forecast lead. Station observations remain temperature
labels only. Prediction requests must supply forecast pressure, rather than
observed pressure; pressure artifacts require it on every row. Existing
five-feature artifacts remain supported by the new code.

The configured `splits` are a template: each run shifts all boundaries by the
same offset until the exclusive test end is 00:00 UTC on the training date.
This preserves durations (364 train, 179 validation, 184 test days in the
provided template) and both two-day gaps. It excludes the incomplete training
day. Without an override, the date is today in UTC, including for CronJobs.
Resolved split dates are saved in the model configuration and MLflow bundle;
resolved configuration and feature choices scope the data/output cache.
Source publication can lag: inspect matched rows and coverage before promotion.
The split end is a requested boundary, not a claim that every observation is
already published.

Preview without downloading or training:

```sh
uv run --locked python - <<'PY'
from pathlib import Path
from meteo_mlops.config import load_config
print(load_config(Path("experiments/pressure-rolling.json"), "2026-10-09")["splits"])
PY
```

Run locally after selecting the intended tracking store:

```sh
uv run --locked meteo-baseline run \
  --config experiments/pressure-rolling.json \
  --training-date 2026-10-09 \
  --data data/experiments/pressure-rolling \
  --output artifacts/experiments/pressure-rolling \
  --evaluate
```

Omit `--training-date` for daily rolling behavior; retain it to reproduce a run.
Find `tracking.json`, `metadata.json`, `evaluation.md` and `test-predictions.csv`
beneath the resulting configuration-hash output directory. `--evaluate` does
not promote or deploy. Promotion compares both models on the candidate's rows;
the old champion uses its own five-feature contract and ignores added pressure.

For scheduled training, release compatible code/images, then place the complete
experiment JSON in `training.config` in the owning values file. Keep scheduling
suspended until a manual run is verified. Deploy the pressure model pin with
compatible inference/training images; an older inference image rejects its
feature contract. Update prediction clients to send `pressure_msl_hpa` before
serving that model. Verification CSVs for it also require this column.

## 4. Manually train a local experiment

Select the tracking store from section 2, then run:

```sh
uv run --locked meteo-baseline run \
  --config experiments/ridge-xgb-v1.json \
  --data data/experiments/ridge-xgb-v1 \
  --output artifacts/experiments/ridge-xgb-v1
cat artifacts/experiments/ridge-xgb-v1/tracking.json
cat artifacts/experiments/ridge-xgb-v1/evaluation.md
```

`run` fetches cached-or-new source responses, fits/selects models, writes the
bundle, logs an MLflow run and registers the selected model. `fetch` performs
only retrieval; `train` uses an existing compatible cache. Use a new directory
when changing configurations, especially dates. Preserve original raw bytes for
reproduction; clearing a cache may retrieve revised upstream observations.

Outputs include `model.joblib`, fitted reference candidates, `metadata.json`,
`metrics.json`, `evaluation.md`, `test-predictions.csv` and `tracking.json`.
The last file gives the Run ID and numeric model version. Inspect corresponding
MLflow artifacts before approval. Loading joblib requires trusted artifacts.

To train and immediately record a lifecycle decision, add `--evaluate` to the
same command. That flag additionally appends a configuration hash to **both**
data and output paths; find the resulting file with:

```sh
find artifacts/experiments/ridge-xgb-v1 -name tracking.json -print
```

Without `--evaluate`, raw paths above are used and evaluation is a separate
command. With it, a failed evaluation can make the command fail after training
already registered a model; inspect the run rather than assuming nothing was
created. Neither form promotes or deploys a model.

## 5. Manually train on the AWS instance

Here “on the instance” means a Kubernetes Job on the k3s node, using the deployed
training image and resources. There is no need to install a separate Python
environment in an SSM shell. Select the AWS kubeconfig and verify the CronJob:

```sh
export KUBECONFIG="$PWD/.aws-k3s/kubeconfig"
kubectl -n meteo-mlops get cronjob training -o yaml
kubectl -n meteo-mlops get jobs,pods
```

If using the deployed configuration unchanged:

```sh
JOB="training-$(date +%s)"
kubectl -n meteo-mlops create job "$JOB" --from=cronjob/training
```

For **one custom parameter experiment**, reuse the JSON from section 3 and create
an independent ConfigMap and Job. This procedure targets the Helm/GitOps CronJob
layout; older raw-manifest labs need migration or their documented workflow.
It leaves the scheduled `training-config` untouched:

```sh
JOB="training-custom-$(date +%s)"
export JOB
kubectl -n meteo-mlops create configmap "$JOB" \
  --from-file=config.json=experiments/ridge-xgb-v1.json
kubectl -n meteo-mlops create job "$JOB" --from=cronjob/training \
  --dry-run=client -o json > /tmp/meteo-training-job.json
python3 - <<'PY'
import json
import os
from pathlib import Path
path = Path("/tmp/meteo-training-job.json")
job = json.loads(path.read_text())
name = os.environ["JOB"]
spec = job["spec"]["template"]["spec"]
config = next(v for v in spec["volumes"] if v["name"] == "config")
config["configMap"]["name"] = name
container = next(c for c in spec["containers"] if c["name"] == "training")
work = f"/training/{name}"
container["command"] = ["sh", "-c",
    f"mkdir -p {work} && cp /app/uv.lock {work}/uv.lock && cd {work} && "
    "exec meteo-baseline run --evaluate --config /config/config.json "
    f"--data {work}/raw --output {work}/baseline"]
path.write_text(json.dumps(job))
PY
kubectl create -f /tmp/meteo-training-job.json
```

The custom Job inherits the deployed image, MLflow endpoint, security settings,
resource limits, deadline and training PVC. Separate directories prevent output
collisions. Run one manual Job at a time and avoid overlap with scheduled training:
CronJob `Forbid` serializes its own scheduled Jobs only. The single 4GiB AWS node
also hosts serving, tracking and optional monitoring/Argo controllers.

Follow either Job:

```sh
kubectl -n meteo-mlops logs -f "job/$JOB" --pod-running-timeout=300s
kubectl -n meteo-mlops wait --for=condition=complete "job/$JOB" --timeout=1800s
kubectl -n meteo-mlops describe job "$JOB"
```

Find the run/version in AWS MLflow. The custom image runs `--evaluate`; logs
include the lifecycle decision. Inspect failed Jobs and MLflow before retrying;
retries create new runs. After retaining the run/configuration evidence, remove
only this custom Job and its ConfigMap:

```sh
kubectl -n meteo-mlops delete job "$JOB"
# Custom-configuration path only:
kubectl -n meteo-mlops delete configmap "$JOB"
```

Output directories remain on the training PVC; MLflow/S3 remains the tracked
artifact record. Do not delete shared PVCs to clean up an experiment.

## 6. Evaluate and explicitly approve a registered version

Connect your workstation CLI to the registry holding the candidate. Optionally
mark it for review, then evaluate (replace `7`):

```sh
uv run --locked meteo-tracking alias candidate 7
uv run --locked meteo-lifecycle evaluate 7
```

Two evaluations have different purposes:

- **Training evaluation:** scores all methods on train/validation/test and
  exports the validation winner. Test scores describe that fixed holdout.
- **Lifecycle evaluation:** recomputes candidate and champion predictions on
  the candidate bundle's exact held-out rows. With no champion it compares
  against raw forecasts to establish the first champion.

The lifecycle gate requires RMSE improvement of at least **0.02 °C**, no increase
in MAE and no increase in absolute bias. Metrics must be finite and valid; row
count must meet the candidate configuration's minimum. A champion cannot be
scored on rows overlapping its training/validation interval plus the 24h10m
embargo. Historical champion aggregate scores are not a fair substitute for
rescoring it on the candidate's rows.

The CLI exposes no threshold override flags; `0.02` improvement and zero
tolerance are its current defaults. Review `lifecycle/decision.json` in the
candidate run: reference version, metrics, window, sample count and rationale.
A rejected decision leaves aliases unchanged. Invalid windows/data can raise
an error instead of producing an ordinary rejection decision.

After a passing decision and review of provenance, compatibility and held-out
results:

```sh
uv run --locked meteo-lifecycle promote 7
```

Promotion reevaluates against the current champion, records a model-version
approval tag, saves the old champion as `previous`, and moves `champion`.
`meteo-tracking alias champion 7` uses the same gate. Serialize alias writes:
MLflow has no transactional compare-and-swap. The command checks for champion
changes during evaluation but cannot prevent simultaneous final writes.

Implementation detail for interpreting tags: evaluation writes
`promotion.approved` on the **run** as gate eligibility; successful promotion
writes it on the **model version** as approval. Rollback checks the model-version
tag. Materialization and Helm serving do not enforce that approval themselves;
operators must select an approved version.

## 7. Deploy the approved model

First materialize and check the specific version from the intended registry:

```sh
uv run --locked meteo-lifecycle materialize 7 --output artifacts/release-7
shasum -a 256 artifacts/release-7/model.joblib
MODEL_PATH=artifacts/release-7/model.joblib \
  uv run --locked uvicorn meteo_mlops.api:app --port 8000
```

Materialization produces `model.joblib` and adjacent `registry.json` containing
the numeric version/checksum. This local API test identifies both fields. It
uses port 8000; stop an existing inference port-forward before starting it.
Stop the test server before forwarding the cluster service on that same port.

For an **Argo-owned AWS application**, edit `gitops/aws/values.yaml`:

```yaml
inference:
  registryVersion: "7"
  qualityConfigMap: inference-verification
```

Merge this into the existing `inference` mapping, preserving other fields and
both application image tags. The verification reference is illustrative:
retain it only with awareness that scores for the old checksum become unavailable
until verification is refreshed. Review/commit/merge the model release, then:

```sh
python3 scripts/argocd-demo.py refresh
python3 scripts/argocd-demo.py status
# Review the compared Git revision and diff in Argo before syncing:
python3 scripts/argocd-demo.py sync
kubectl -n meteo-mlops rollout status deployment/inference --timeout=300s
```

For **manual Helm before Argo adoption**, set the numeric pin in the appropriate
local/AWS values file and deploy that complete file using [the Helm guide](cicd-helm.md).
Do not run raw deploy helpers or Helm application upgrades against Argo ownership.

A pin change restarts inference. The training-image init container downloads
that exact version from the cluster's MLflow endpoint into an emptyDir, then the
API loads the model once. The endpoint must resolve the same version you approved;
workstation materialization does not upload it to the cluster. Registry/S3 access
is required at each new pod startup. Use compatible application/training images.

Keep an inference port-forward open and verify:

```sh
curl -fsS http://127.0.0.1:8000/ready
curl -fsS http://127.0.0.1:8000/predict \
  -H 'Content-Type: application/json' \
  -d '{"forecasts":[{"valid_time":"2026-01-01T12:00:00Z","forecast_c":18.0}]}'
```

Require registry version `7`, the expected full checksum, finite predictions,
completed rollout and Argo `Synced/Healthy` where applicable. Update explicit
observation verification using [observability](observability.md); a smoke request
establishes operation, not current forecast accuracy.

An empty `inference.registryVersion` retains the legacy checksum-named model
Secret path. Its Secret must exist before deployment and fit Kubernetes' size
limit. Prefer numeric registry pins for this lifecycle workflow. Neither path
polls `champion` during requests or automatically reloads on alias changes.

## 8. Roll back and retain release evidence

Record the actual serving version before release; `previous` is a registry
history pointer and may not match that deployed release. To return the registry
to an earlier approved, finished version:

```sh
uv run --locked meteo-lifecycle rollback 6
```

Then separately set `inference.registryVersion: "6"` in the owning values,
review/deploy it and repeat readiness/checksum/prediction verification. With
Argo, revert the serving-pin Git commit or commit the explicit previous pin,
then refresh/sync. Alias rollback alone leaves serving unchanged. The rollback
command does not reevaluate the gate or rotate `previous`; it records a rollback
run and moves champion to the approved target.

Retain registry metadata, S3/local bundle bytes, compatible immutable images,
configuration/raw-data provenance and release records. Git rollback restores
references, not MLflow database contents, artifacts or training outputs.

## 9. What is automated today?

| Trigger | Automated behavior | Operator responsibility |
| --- | --- | --- |
| `meteo-baseline run` | Fetch/cache, fit candidates, select validation winner, score, log and register | Choose config/store, inspect evidence |
| Add `--evaluate` | Config-hash cache/output paths and lifecycle decision | Review; promotion remains explicit |
| Enabled Helm CronJob | Runs `run --evaluate` on its deployed config | Choose parameters/dates, enable schedule, keep node running |
| `promote VERSION` | Recompute gate, approval tag, previous/champion aliases | Authorize promotion and deploy serving pin |
| New serving pod with numeric pin | Download exact bundle, check checksum, load once | Select approved version and verify rollout |
| Main source CI build | Tests, immutable inference/training image publication, image-release PR | Review/merge PR; check hosted run actually succeeded |
| Argo manual sync | Apply reviewed compared Git revision, track health/drift | Review diff and request sync; drift is not corrected automatically |
| Prometheus scrapes | Platform metrics and available model/verification scores | Supply fresh matched observations and verification ConfigMap |

Scheduled training is **suspended by default**. To enable it, change
`training.suspend: false` in the owning values and deploy. Default schedule:
Sunday 03:00 UTC (Saturday 17:00 Pacific/Tahiti). Configure `training.schedule`
and the complete `training.config` there; a one-off custom Job does not change
future scheduled parameters. With GitOps, commit/sync these choices rather than
patching the live CronJob.

CronJob concurrency is `Forbid`, retries are zero, deadline is 1800 seconds and
missed starts have a 300-second deadline. A stopped AWS node cannot run training.
Identical inputs can produce distinct runs/versions; this is not exactly-once
registration. `--evaluate` scopes caching by full configuration hash; it does
not refresh cached upstream responses automatically.

There is no hyperparameter-search service,
champion promotion, model-release PR, deployment on alias change, live alias
reload, current-observation ingestion/verification refresh, or quality-triggered
rollback. Image-release automation preserves model/verification choices.
`scripts/aws-k3s.sh deploy-model champion` is not implemented.

## 10. Verification boundaries

Existing tests cover a temporary SQLite registry, eligibility/rejection,
explicit promotion, pinned serving, checksum failure, rollback and Helm render
paths. This rewritten guide was checked against the CLI, configuration validator
and chart templates; it does not record a new training or live deployment.

For end-to-end acceptance, run a custom Job, inspect its registered bundle and
decision, promote an eligible version, deploy its numeric pin, verify API identity
and predictions, then deploy an approved rollback pin and verify again. Scheduled
execution, AWS S3 access, Argo adoption and hosted CI each need their own live
evidence; see [operations](operations.md) and the dated stage acceptance records.
