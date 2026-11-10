# EKS exercise validation (2026-10-07, Pacific/Tahiti)

The user requested manual teardown after deployment, then confirmed **everything is removed and clean** on 2026-10-07. AWS `DescribeCluster` independently returned `ResourceNotFoundException` for `meteo-mlops-eks-exercise`. Full resource cleanup is recorded as operator-confirmed; the agent did not rerun the complete orphan-resource audit. the implementation live acceptance and the implementation deployment/comparison/teardown acceptance are recorded; code review/merge remains separate.

## Provisioning and integrations

* Terraform applied **32 added, 0 changed, 0 destroyed**, in the independent `infra/eks` state. Existing k3s resources and ECR repositories were reused only where explicitly referenced; Terraform did not change them.
* Cluster `meteo-mlops-eks-exercise`, Oregon `us-west-2`, Kubernetes 1.35, standard support; restricted operator public `/32` API access plus private node access.
* One Ready on-demand `t4g.large` ARM managed node, Kubernetes `v1.35.8-eks-3b4a6ca`. Control-plane creation took about 11 minutes, then node/add-ons a few more minutes.
* Managed add-ons ACTIVE: VPC CNI `v1.23.2-eksbuild.1`, CoreDNS `v1.14.6-eksbuild.4`, kube-proxy `v1.35.3-eksbuild.29`, Pod Identity agent `v1.4.0-eksbuild.3`, EBS CSI `v1.66.0-eksbuild.1`.
* MLflow STS identity verified as `assumed-role/meteo-mlops-eks-exercise-mlflow/...`, rather than the node role. No static AWS keys or ECR pull Secrets were added to application pods.
* MLflow/training PVCs Bound to encrypted gp3, 2 GiB each; Prometheus PVC 1 GiB. Root disk 20 GiB. Exact IDs were captured in private `.eks/cleanup-inventory.json`: **1 instance, 4 volumes**, plus the managed ASG, bucket, roles and VPC. The read-only capture helper ran successfully; the agent did not execute the post-destroy verifier; the operator subsequently confirmed complete cleanup.

## Application and workflow

The existing immutable image tag `ab584cd25aed4f2895d15125660309d457d6c4da` deployed through the same Helm chart. Inference's explicitly selected trusted baseline file SHA-256 is `15227624da3accdfbe955a937d33aa104e1cb031d4d849a55cafe8451fadcf88`; `/ready` reported that full checksum, `mean_bias_correction`, station NZWN and 24-hour horizon. No automatic promotion occurred.

`scripts/kubernetes_smoke.py` passed prediction, deliberately failed image rollout/rollback, training completion, MLflow artifact retrieval, direct S3 object SHA-256 comparison, and unchanged MLflow run metadata/artifact checksum after pod restart. Training Job completed in 32 seconds. Run ID: `7f9d82fa553c4a03adee21b8d079f716`. New training artifact SHA-256: `129a7d2f4f0e7cbf76e11640f90398510b25e8248d90aaad2a29c692f27bd7f0`. This artifact was validated but **not** deployed as the inference model.

Test period: 2025-07-01 through 2025-12-31 UTC, 4,416 hourly samples. Raw RMSE 1.458066 °C; mean-bias correction 1.200024 °C; ridge 1.118435 °C; XGBoost 1.120762 °C. These are the existing demonstrator's evaluation, not an independent operational forecast validation. Input station NZWN (Wellington Airport). Forecasts: Open-Meteo Previous Runs API `gfs_global`, `temperature_2m_previous_day1` (24-hour fixed offset). Observations: independent Iowa Environmental Mesonet ASOS routine METAR `tmpc`, report type 3, matched to UTC hours within 10 minutes. Inputs span 2024-01-01 through 2025-12-31; the existing [baseline data provenance and split rules](baseline.md) document source URLs and observation fields.

## GitOps and monitoring

Argo CD `v3.5.4` ran with all seven controller/support pods Ready. The same read-only GitHub App/key was reused at the user's request: only the named repository credential was transferred in process memory, without logging its data; the temporary k3s SSM tunnel was then closed. The restricted AppProject and EKS branch Application compared successfully, synced to Healthy/Synced, detected a deliberate ConfigMap mutation as OutOfSync, and restored the Git value through manual sync. Training remains suspended and EKS's values do not redirect the existing k3s release pipeline.

Monitoring smoke passed: **4 targets up** (`cadvisor`, `inference`, `kube-state-metrics`, `prometheus`); PromQL for **all 22 dashboard panels** valid; model/prediction/pod/resource series present; Grafana datasource/dashboard healthy and provisioned. Application, Argo and monitoring Services remain ClusterIP, accessed by localhost port-forward.

The operational screenshot is omitted from this public snapshot to avoid exposing deployment-specific hostnames and addresses.

## Local and hosted checks

Terraform validate and two mocked safety tests passed; both Helm charts lint/render; shell syntax and Ruff passed. All 67 repository tests passed, including the new EKS Helm integration test (MLflow-only ServiceAccount, gp3 PVCs, private Services). Hosted [Terraform validation for both environments](https://github.com/infraria-labs/mlops-demo/actions) passed.

## Manual teardown completion

The user confirmed all exercise resources were removed and clean. The EKS cluster is absent. During the exercise, the estimated recurring base was **$0.1722/hour ($4.13/day)** for standard EKS, the on-demand node and its public IPv4. Root/PVC EBS, S3, transfer and potential surplus CPU credits are additional; prices were verified through AWS Pricing API/docs, not measured billing.

For future exercises, follow [the exact teardown sequence](eks.md#teardown-and-verification-inf-85): capture inventory, delete workloads/PVCs while CSI is alive, review/apply the EKS-only destroy plan, then run `scripts/eks_cleanup.py verify`. An empty Terraform state alone does not prove CSI disks are removed. Existing k3s and shared ECR resources remain outside that teardown.
