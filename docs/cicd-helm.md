# CI/CD and Helm

Purpose: package workloads and install/adopt them with Helm. Prerequisites: a ready cluster, Helm 3, published/imported images and a trusted model. Direct CI-to-SSM deployment is historical; the checked-in workflow now proposes [GitOps releases](gitops.md). Manual application upgrades below apply only before Argo adoption.

The direct SSM/Helm CI deployment described below is the historical learning
stage. the implementation replaces it with [Argo CD GitOps](gitops.md). Keep these manual
Helm steps for unmanaged labs only; do not run them against an Argo-owned app.

The original `k8s/local` manifests and AWS renderer remain as the learning labs.
`charts/meteo-mlops` packages the same inference, MLflow, configuration, PVCs,
training CronJob and optional Job. One release per namespace is supported because
resource names stay compatible with the raw labs. The chart does not contain
model bytes or kubeconfig. Training remains suspended; image deployment never
promotes a model.

## Historical direct-deployment pipeline

Pull requests run Ruff, locked pytest, Helm lint/render checks and separate
inference/training image builds with GitHub build/dependency caches. Inference
smoke checks readiness, prediction parity, invalid input, non-root execution and
Docker health. Training smoke runs deterministic synthetic training, reloads the
selected artifact and checks MLflow registration without archive downloads.

Main runs the same gates, assumes an AWS role using OIDC, publishes both Docker
targets for amd64 and arm64 to the existing immutable ECR repositories under the
full Git SHA, then deploys with Helm through SSM Run Command. Retries reuse
already-published SHA tags; ECR errors other than ImageNotFound fail the job.
The existing Docker default remains the inference target. The training target
has the same locked runtime with a training CLI default and no HTTP healthcheck.

The AWS release job runs in the `aws-demo` GitHub environment. Releases are
serialized and are not cancelled mid-deployment. A failed Helm upgrade rolls
back automatically (`--atomic --wait`, ten-minute timeout). CI also checks both
Deployment rollouts and propagates failed/timed-out SSM results. Run Command
executes on the node; no Kubernetes credentials leave the instance. The packaged
chart is sent inline (24 KB limit checked before submission), and a fixed Helm
3.17.3 ARM binary is downloaded to a temporary directory and checksum-verified.
The current inference Deployment identifies the existing trusted model secret;
CI requires that secret to exist and preserves its name.

## One-time AWS setup

1. Set `github_repository = "infraria-labs/mlops-demo"` in Terraform variables.
   If the account already has GitHub OIDC, also set `github_oidc_provider_arn`.
   Query the repository's exact OIDC subject prefix:

   ```sh
   gh api repos/infraria-labs/mlops-demo/actions/oidc/customization/sub
   ```

   If the response includes `sub_claim_prefix`, set `github_oidc_subject_prefix`
   to that exact value in Terraform variables. This repository uses
   `repo:infraria-labs@123456789/mlops-demo@987654321`; a name-only trust
   subject will reject its GitHub login. Terraform appends `:environment:aws-demo`.
   Review `terraform plan`, then apply. The optional CI role only grants ECR
   publication to the two repositories and Run Command to this node. SSM
   invocation polling and ECR authorization require wildcard resource scope.
   Run Command is administrative access to this demonstrator node.
2. In GitHub, create environment **aws-demo**, restrict deployment branches to
   **main**, and set these environment variables from Terraform outputs:

   | Variable | Terraform output |
   | --- | --- |
   | AWS_ROLE_ARN | github_role_arn |
   | AWS_REGION | region |
   | INSTANCE_ID | instance_id |
   | INFERENCE_REPOSITORY | repositories.inference |
   | TRAINING_REPOSITORY | repositories.training |
   | ARTIFACT_DESTINATION | artifact_destination |

   The OIDC trust binds the exact repository, environment and STS audience.
   GitHub's environment branch restriction is essential because an environment
   OIDC subject does not contain the branch. Require the `quality` and both
   `images` checks in main's branch protection. No long-lived AWS keys are used.
3. If starting from a new AWS installation, follow [the AWS lab](aws-k3s.md)
   once to create the cluster, deploy the initial workloads and install a trusted
   model. If inference and MLflow are already running, skip that setup and follow
   **AWS migration to Helm** below before enabling main releases.

## Before running Helm

Run all commands from the repository root:

```sh
cd /path/to/mlops-demo
```

Install Helm 3 first (3.17.3 or a newer **3.x** release supports raw-resource
adoption). On macOS:

```sh
brew install helm@3
export PATH="$(brew --prefix helm@3)/bin:$PATH"
```

The deployment commands below use Helm 3 flags; the default Homebrew `helm`
formula installs Helm 4. Confirm the selected version with `helm version --short`.

Choose the section for your target: **Local k3d installation** runs on your Mac;
**AWS migration to Helm** updates the existing AWS cluster. Each section sets
its own kubeconfig.

## Local k3d installation

Create the local cluster only if `k3d cluster list` does not show `meteo-mlops`:

```sh
scripts/k3d-local.sh create
```

Then use the generated local kubeconfig and a trusted model artifact:

```sh
export KUBECONFIG="$PWD/.k3d/kubeconfig"
tag=$(git rev-parse HEAD)
docker build --target inference -t "meteo-inference:$tag" .
docker build --target training -t "meteo-training:$tag" .
k3d image import "meteo-inference:$tag" "meteo-training:$tag" -c meteo-mlops
kubectl create namespace meteo-mlops --dry-run=client -o yaml | kubectl apply -f -
secret="inference-model-$(shasum -a 256 artifacts/baseline/model.joblib | cut -c1-12)"
kubectl -n meteo-mlops create secret generic "$secret" \
  --from-file=model.joblib=artifacts/baseline/model.joblib --dry-run=client -o yaml | kubectl apply -f -
kubectl -n meteo-mlops patch secret "$secret" --type=merge -p '{"immutable":true}'
helm upgrade --install meteo-mlops charts/meteo-mlops -n meteo-mlops \
  -f charts/meteo-mlops/values-local.yaml \
  --set-string "images.inference.tag=$tag" --set-string "images.training.tag=$tag" \
  --set-string "modelSecret=$secret" --wait --wait-for-jobs --timeout 10m
```

For a namespace already containing raw lab resources, review `helm template`
against the current manifests and back up MLflow metadata/model identifiers.
Add **`--take-ownership` only to the first upgrade** to adopt the existing
ConfigMaps, Services, Deployments, CronJob and PVCs. Keep their names and selectors
unchanged. Do not use `--atomic` on the first adoption: an installation rollback
can remove adopted resources. Subsequent CI upgrades use `--atomic`; CI never
silently adopts resources. The chart retains PVCs on uninstall, but deleting the
namespace still deletes its PVCs. Never uninstall the release to roll it back.

## AWS migration to Helm

The existing AWS workloads and model are required. The completed
`charts/meteo-mlops/values-aws.yaml` already contains the ECR repositories,
published immutable image tags, AWS region, S3 destination and model-secret name.
Use it directly; you do not need to export additional deployment variables.

**Terminal 1 — start the AWS tunnel and leave it running:**

```sh
cd /path/to/mlops-demo
scripts/aws-k3s.sh tunnel
```

**Terminal 2 — select and check the AWS cluster:**

```sh
cd /path/to/mlops-demo
export KUBECONFIG="$PWD/.aws-k3s/kubeconfig"
test -f "$KUBECONFIG"
kubectl -n meteo-mlops get deployments
```

Both `inference` and `mlflow` should be ready. If the kubeconfig file is missing,
retrieve it with `uv run --locked python scripts/aws-kubeconfig.py`, then repeat
the check. If the connection is refused, check the tunnel in Terminal 1.

**Preview the chart, then adopt the AWS resources:**

```sh
helm lint charts/meteo-mlops -f charts/meteo-mlops/values-aws.yaml --strict
helm template meteo-mlops charts/meteo-mlops -n meteo-mlops \
  -f charts/meteo-mlops/values-aws.yaml
```

Review the rendered images, model-secret reference and PVC names against the
existing workloads. Then run:

```sh
helm upgrade --install meteo-mlops charts/meteo-mlops -n meteo-mlops \
  -f charts/meteo-mlops/values-aws.yaml \
  --take-ownership --wait --wait-for-jobs --timeout 10m
```

`--take-ownership` is for this first migration. It adopts the existing resources
without deleting them. Keep `--atomic` off for the first adoption: a failed
installation rollback can remove adopted resources.

**Confirm the migration:**

```sh
helm status meteo-mlops -n meteo-mlops
kubectl -n meteo-mlops rollout status deployment/inference --timeout=300s
kubectl -n meteo-mlops rollout status deployment/mlflow --timeout=300s
kubectl -n meteo-mlops get pods,pvc
```

Helm should report `STATUS: deployed`, both rollouts should complete and both
PVCs should be `Bound`. If Helm fails, inspect `helm status` and
`kubectl -n meteo-mlops get events --sort-by=.metadata.creationTimestamp` before
retrying. Do not uninstall the release to recover a failed adoption.

If you see **`image repository is required`**, remove old `--set-string` flags
that reference unset shell variables. An empty override replaces the populated
value in `values-aws.yaml`. The command above needs no such overrides.

At this historical stage, CI handled Git-SHA image upgrades and atomic rollback.
The checked-in workflow now publishes images and proposes GitOps release PRs.
For a manual upgrade, update the image tags in `values-aws.yaml` to tags already
published in ECR, then run the command below with the AWS kubeconfig and tunnel:

```sh
helm upgrade meteo-mlops charts/meteo-mlops -n meteo-mlops \
  -f charts/meteo-mlops/values-aws.yaml \
  --atomic --wait --wait-for-jobs --timeout 10m
```

## Chart configuration and training

Namespace and
model secret are deliberately outside Helm ownership. The optional Job is off
by default. To execute training, use `kubectl create job training-<unique-name>
--from=cronjob/training`; alternatively enable `training.job.enabled` and use a
unique `training.job.name` per run to avoid immutable Job spec updates.
A small storage initialization Job binds the training PVC and checks its write
permissions even while the CronJob is suspended; otherwise local-path
WaitForFirstConsumer prevents Helm from completing a fresh install. The Job has
a unique name per release revision and expires after five minutes.

Resources, training experiment configuration, inference replicas, PVC sizes/class, image repositories/tags, pull policy, schedule,
suspension and model secret are configurable in chart values. The default local
`k3d-v1` tags support the earlier lab; use SHA tags for traceable new builds.
AWS values contain the current demonstrator repositories, immutable image tag,
region, S3 destination and existing versioned model-secret reference. They can
render without overrides. CI overrides image tags with each release Git SHA and
preserves the live model secret. Refresh these values if the infrastructure or
model changes.

## Rollback and recovery

First select the target kubeconfig. For AWS, keep the SSM tunnel running and use
`export KUBECONFIG="$PWD/.aws-k3s/kubeconfig"`. For local k3d, use
`export KUBECONFIG="$PWD/.k3d/kubeconfig"`. Choose a successful revision from
`helm history` and replace `PREVIOUS_REVISION` below with its number:

```sh
helm history meteo-mlops -n meteo-mlops
helm rollback meteo-mlops PREVIOUS_REVISION -n meteo-mlops --wait --wait-for-jobs --timeout 10m
kubectl -n meteo-mlops rollout status deployment/inference --timeout=300s
kubectl -n meteo-mlops rollout status deployment/mlflow --timeout=300s
```

History retains ten revisions. Keep SHA-tagged ECR images and versioned model
secrets needed by those revisions. Helm rollback restores workload image/config
and secret references; it does not reverse training output, S3 artifacts or
MLflow database changes. Verify `/ready` and a prediction after rollback. If SSM
polling times out, inspect the command and Helm status before retrying; do not
start a concurrent upgrade. Argo CD now replaces the direct deployment step in the checked-in workflow;
complete the adoption checks before cutting over the live environment.

## Verification

```sh
uv run --locked ruff check .
uv run --locked pytest
docker build --target training -t meteo-training:smoke .
uv run --locked python scripts/training_container_smoke.py --image meteo-training:smoke
uv run --locked python scripts/container_smoke.py
helm lint charts/meteo-mlops --strict
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test
```

Helm tests skip if Helm is absent locally; CI installs the fixed version and
runs them explicitly. Cloud acceptance also requires the one-time IAM/environment
setup, adoption and a successful main workflow; local tests do not prove OIDC,
ECR publication or AWS rollout.

Implementation verification on 2026-10-05: Ruff and 44 pytest checks passed,
including local/AWS chart rendering, unsafe-tag rejection and deployment-command
checks. Both final training/inference Docker targets built and passed their
container smoke checks on ARM64. Terraform validate and both mocked plans passed. An isolated k3d cluster
verified Helm installation and upgrade, both Deployment probes, PVC binding and
storage Job completion, a prediction through the Kubernetes Service and Helm
rollback. Cloud OIDC, ECR publication and AWS rollout still require
the setup above and a successful GitHub Actions release.

References: [GitHub OIDC for AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
and [Helm 3 upgrade flags](https://docs.helm.sh/docs/v3/helm/helm_upgrade/).
