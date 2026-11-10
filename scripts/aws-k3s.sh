#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export KUBECONFIG="$PWD/.aws-k3s/kubeconfig"
tf() { terraform -chdir=infra/terraform "$@"; }
region=$(tf output -raw region)
instance=$(tf output -raw instance_id)
export AWS_DEFAULT_REGION="$region"
kubectl() { command kubectl --kubeconfig "$KUBECONFIG" "$@"; }
case "${1:-}" in
  tunnel)
    aws ssm start-session --target "$instance" --document-name AWS-StartPortForwardingSession \
      --parameters '{"portNumber":["6443"],"localPortNumber":["6443"]}'
    ;;
  publish)
    tag="${2:?Provide a unique immutable image tag}"
    outputs=$(tf output -json)
    inference=$(printf '%s' "$outputs" | python3 -c 'import json,sys; print(json.load(sys.stdin)["repositories"]["value"]["inference"])')
    training=$(printf '%s' "$outputs" | python3 -c 'import json,sys; print(json.load(sys.stdin)["repositories"]["value"]["training"])')
    registry="${inference%%/*}"
    aws ecr get-login-password | docker login --username AWS --password-stdin "$registry"
    tags=()
    for repository in "$inference" "$training"; do
      if aws ecr describe-images --repository-name "${repository#*/}" --image-ids "imageTag=$tag" >/dev/null 2>&1; then
        echo "Already published: $repository:$tag"
      else
        tags+=(-t "$repository:$tag")
      fi
    done
    if [ "${#tags[@]}" -gt 0 ]; then
      docker buildx build --platform linux/arm64 "${tags[@]}" --push .
    fi
    ;;
  deploy)
    tag="${2:?Provide the published image tag}"
    model="${3:-artifacts/baseline/model.joblib}"
    test -f "$model"
    test -f "$KUBECONFIG"
    umask 077
    mkdir -p .aws-k3s
    tf output -json | uv run --locked python scripts/render-aws.py --tag "$tag" > .aws-k3s/manifests.yaml
    kubectl apply -f k8s/local/namespace.yaml
    version=$(shasum -a 256 "$model" | cut -c1-12)
    secret="inference-model-$version"
    kubectl -n meteo-mlops create secret generic "$secret" --from-file="model.joblib=$model" --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n meteo-mlops patch secret "$secret" --type=merge -p '{"immutable":true}'
    sed "s/secretName: inference-model$/secretName: $secret/" .aws-k3s/manifests.yaml | kubectl apply -f -
    kubectl -n meteo-mlops rollout status deployment/mlflow --timeout=300s
    kubectl -n meteo-mlops rollout status deployment/inference --timeout=300s
    ;;
  train)
    kubectl -n meteo-mlops create job "training-$(date +%s)" --from=cronjob/training
    ;;
  stop) aws ec2 stop-instances --instance-ids "$instance" ;;
  start) aws ec2 start-instances --instance-ids "$instance" ;;
  reboot) aws ec2 reboot-instances --instance-ids "$instance" ;;
  *) echo 'Usage: scripts/aws-k3s.sh {tunnel|publish TAG|deploy TAG [model]|train|stop|start|reboot}' >&2; exit 2 ;;
esac
