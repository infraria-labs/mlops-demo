# AWS workload adaptation

Manifests are rendered from `k8s/local` by `scripts/render-aws.py` using Terraform outputs. Deploy with `scripts/aws-k3s.sh deploy TAG [trusted-model.joblib]` after publishing that immutable ARM tag and opening the SSM tunnel. Rendered files and kubeconfig are kept in ignored `.aws-k3s/`.

See [provisioning, validation, cost and teardown](../../docs/aws-k3s.md).
