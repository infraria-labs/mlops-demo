# Optional platform experiments: Kubeflow decision and backlog

Decision for the implementation, 2026-10-08 (Pacific/Tahiti): **do not add Kubeflow
Pipelines (KFP) to the core demonstrator**. This uses the issue's acceptance
alternative: explicitly explain why it is not worth adding to the core workflow.
The subsequent [isolated local learning lab](kubeflow-local.md) explores KFP
without changing that decision or the AWS architecture.

## Why defer KFP here?

The implemented workload recalibrates one station's temperature forecast with
small CPU models. `meteo-baseline run --evaluate` already fetches/cache-checks
data, fits candidates, selects by validation RMSE, registers the bundle in
MLflow and records a lifecycle gate. A Job runs it once; a suspended CronJob
provides scheduling. Promotion and serving-pin changes remain operator actions.
Splitting this short sequential command into independently operated components
has little immediate benefit, while adding another backend and artifact lifecycle.

The [AWS acceptance record](aws-validation.md) documents real training and
persistence; the [EKS acceptance record](eks-validation.md) documents training,
GitOps drift/recovery and monitoring, followed by teardown. These are dated
evidence, not a current health check. This decision is based on repository
architecture and those records; no AWS cluster was resumed or inspected for the implementation.

KFP would become valuable for multiple reusable stages, station/model fan-out,
independent retries or a team needing a visual execution graph. Wrapping the
current command as one component demonstrates submission, but teaches little
about those benefits. The separate lab now has [local measurements](kubeflow-local.md);
the existing shared 4 GiB k3s node is still a capacity constraint, not proof that
KFP cannot run on it. Prefer an isolated experiment when there is a concrete need.

## Architectural comparison

| Concern | Jobs/CronJobs + MLflow today | KFP experiment would add |
| --- | --- | --- |
| Execution | One container owns sequential Python orchestration | A declared graph of container components and dependencies, coordinated by a backend |
| Scheduling | CronJob, suspended by default, `Forbid` concurrency | Pipeline runs/recurring runs; choose one scheduler to avoid duplicate training |
| Data passing | Cache/output directories on training PVC; registry bundle retrieval | Typed component outputs and artifact transfers through a configured pipeline root |
| Failure recovery | Inspect Job/logs and MLflow; rerunning can register another version | Stage visibility and retry controls; registration side effects still need deduplication |
| Reuse | Shared Python package and immutable training image | Reusable component contracts and task caching; inputs must identify data/config/code |
| Evidence | MLflow metrics, provenance, run ID, numeric registry version | Graph/task metadata linked to the MLflow run/version; preserve the existing registry contract |
| Approval/serving | Explicit promotion, reviewed numeric pin, Argo CD manual sync | Keep the same approval boundary; successful pipeline execution is not release approval |
| Operations | Kubernetes workload, PVC, MLflow database/artifacts, Helm/GitOps | Also maintain KFP API/UI/controllers, metadata/database and artifact-store configuration, RBAC, backups and SDK/backend compatibility |

KFP simplifies dependency coordination and passing artifacts between Pods
([pipeline concepts](https://www.kubeflow.org/docs/components/pipelines/concepts/pipeline/),
[artifact handling](https://www.kubeflow.org/docs/components/pipelines/user-guides/data-handling/artifacts/)).
Its [standalone installation](https://www.kubeflow.org/docs/components/pipelines/operator-guides/installation/)
avoids the whole Kubeflow distribution, but still creates a platform to operate.
[Task caching](https://www.kubeflow.org/docs/components/pipelines/user-guides/core-functions/caching/)
can skip work; disable it for registration and evaluation against a mutable
champion unless the reference is made an explicit immutable input. These docs
were consulted on the decision date; pin and review releases before a future lab.

Argo CD reconciles deployed desired state. Argo Workflows orchestrates tasks.
They have different responsibilities; adding Workflows or KFP would not replace
the reviewed serving release process. KFP also does not provide new independent
weather observations, better validation splits or automatic scientific validity.

## Bounded KFP experiment if the decision is revisited

First complete the core release/rollback acceptance in [operations](operations.md)
and [retraining](retraining.md). Then use a disposable local cluster with an
explicit kubeconfig/context, a pinned standalone KFP release and compatible SDK;
verify image architecture, storage and resource headroom before installation.
Do not reuse the retained AWS registry or enable its training schedule.

Use the same NZWN/GFS/24-hour input snapshot, split configuration and immutable
training image as the baseline. Minimum useful graph:

1. Train/register using the existing CLI without `--evaluate`; emit the tracking
   store identity, run ID and numeric version from `tracking.json` as outputs.
2. Evaluate that exact version using `meteo-lifecycle evaluate VERSION` against
   the isolated registry; retain `lifecycle/decision.json` and report metrics.
3. Stop for operator review. Promotion and deployment stay outside this graph.

Pass the version as a parameter between stages; do not pass a workstation path
to another Pod. Provide writable scratch space, the existing configuration and
lockfile requirements, and reachable MLflow/artifact endpoints. An adapter must
emit component outputs; the optional local lab implements this contract.

Accept the experiment only with a successful two-stage run, matching source/data
hashes and metrics within a declared numeric tolerance, traceable KFP-to-MLflow
IDs, a deliberately failed evaluation, and evidence that champion/serving stayed
unchanged. Compare cold/warm duration, idle/peak resources, retry behavior and
setup/maintenance effort with the ordinary Job. A compiled YAML alone is not a
working pipeline. Budget one local lab day; stop if platform setup dominates.

Pause by stopping submissions/recurring runs and deleting the disposable cluster
after exporting evidence. Record residual volumes/object storage and confirm
cleanup. Any future AWS experiment needs its own cost cap and teardown inventory;
there is no new recurring cloud cost or teardown requirement from this decision.

## Post-core backlog

These are proposed experiments, not new requirements or implementations. Select
one at a time after core acceptance; ordering reflects this demonstrator's needs.

| Order | Candidate / learning value | Small acceptance experiment | Revisit trigger / added burden |
| --- | --- | --- | --- |
| 1 | Evidently: inspect weather/model drift | Compare two fixed windows; retain report, provenance and seasonal interpretation; no automatic promotion | Fresh observation ingestion exists; reporting dependency and reference-window maintenance |
| 2 | OpenTelemetry: trace requests across services | Correlate one inference request with a trace and existing metrics; measure overhead | Multi-service debugging need; collector/exporter and retention operations |
| 3 | Argo Workflows: general container DAGs | Train then evaluate with explicit registry version; inject failure and compare with Job | Independent stages/retries needed; controller, CRDs and artifact handling |
| 4 | Kubeflow Pipelines: ML graph/component reuse | Run the isolated two-stage experiment above | Several reusable ML stages or station fan-out; backend/storage/SDK operations |
| 5 | KServe: standardized model serving | Serve one approved immutable bundle through an adapter and compare API/readiness/rollback | Multiple models justify serving abstraction; runtime/controller and optional networking dependencies |
| 6 | KEDA: event-driven scaling | Queue a bounded workload, scale from idle and verify drain/no duplicate work | A real queue or external event source exists; scaler credentials and scaling policies |
| 7 | Karpenter: node provisioning economics | Disposable EKS capacity test with limits, scheduling evidence and complete cleanup | Variable multi-node demand; EKS cost, IAM, disruption and node lifecycle |
| 8 | Distributed training: compute scaling | Benchmark fixed data/model at one versus multiple workers; report communication cost and reproducibility | Training outgrows one node; current small ridge/XGBoost work has little justification |

Promote an experiment into the architecture only when measured benefit exceeds
its maintenance cost. Keep core training, MLflow and serving usable without it.
