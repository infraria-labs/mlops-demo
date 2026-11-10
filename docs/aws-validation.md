# AWS validation — 2026-10-05 (Pacific/Tahiti)

This is a dated acceptance record, not a live inventory. Instance IDs, versions and stopped/running state below describe that verification session. Use [the AWS runbook](aws-k3s.md) for procedures and [operations](operations.md) to inspect current state.

Environment: `default` AWS profile, `us-west-2`, `demo`, branch `inf-61-05-provision-low-cost-aws-environment-with-terraform`.

## Verified

- Terraform applied the 25-resource stack in the selected account. No existing infrastructure was changed. Final mock-provider test passes; configuration validation passes.
- Python suite: 35 passed. AWS manifest test, shell syntax and Python compilation pass.
- ARM Docker image builds and the container smoke test passes with the actual trained model (health/readiness/prediction, invalid-input handling and container health).
- EC2 `i-0123456789abcdef0` boots pinned k3s `v1.33.9+k3s1`; cloud-init completes and node is Ready.
- `/var/lib/rancher` mounts encrypted gp3 EBS `vol-0123456789abcdef0`; system pods are Running. Terraform stops the instance before any future volume detachment.
- Node-role ECR credential generation succeeds (credential values redacted). Remote kubectl works through the SSM localhost tunnel. Kubeconfig is ignored and mode 0600.
- Live security group has no ingress rules and only TCP/443 egress.
- MLflow pod obtains node-role credentials through IMDSv2 and successfully lists its private S3 prefix.
- AWS-rendered workloads pass Kubernetes server-side dry run. All Services remain ClusterIP, and training schedule remains suspended.
- Cost Explorer service dimensions are readable. The query covered completed UTC days through 2026-10-05, preceding this deployment in UTC, and returned no Project-attributed charges. It does not demonstrate zero cost or a measured recurring bill. AWS Pricing API confirms Oregon Linux t4g.medium at $0.0336/hour.

- Both ECR repositories hold immutable tag `inf61-20261006-v1` with the same ARM application manifest. Inference and MLflow roll out successfully.
- The repaired full AWS smoke test passes using the training-repository image: inference, failed rollout/rollback, real training, direct S3 model checksum comparison, and metadata/artifact persistence across MLflow pod restart.
- Final training run: `381c5b77f8ac4bd4abde2c8d552bead7`, registered model version 2. S3/MLflow model SHA-256: `8b1e2494edb5313f5bbdc89eca2e2769ec0e683a4a7072f8daf4053d3314bba3`.

- Instance reboot passes: boot ID changed, EBS remounted, node returned Ready, and the recorded final run, checksum, metrics, aliases and PVC identities are unchanged.
- Stop/start passes with the same persistence evidence and restored inference readiness. Public IPv4 is absent while stopped; SSM access uses instance ID.

## Final state and limits

The demo is confirmed stopped after validation, with no public IPv4, retaining EBS, S3 and ECR. Final Terraform validation/mock test passes and the real plan returns exit code 0 (no changes) while stopped. The launch-time public-IP refresh comparison is ignored to prevent pausing from proposing node replacement. Resume with `AWS_PROFILE=default scripts/aws-k3s.sh start`, wait for SSM/k3s readiness, then open `scripts/aws-k3s.sh tunnel`.

Cost observations still need billing ingestion and activated cost allocation tags; the monthly amounts are planning estimates. A >12-hour ECR credential-expiry soak and historical local MLflow metadata migration were not performed. Current AWS runs use S3 and remain retrievable after restarts. No full destructive teardown was executed because it would erase the retained demo; the cleanup procedure is documented. Estimates and operations are in [the AWS runbook](aws-k3s.md).
