#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export KUBECONFIG="$PWD/.k3d/kubeconfig"
image=meteo-inference:k3d-v1
kubectl() { command kubectl --context k3d-meteo-mlops "$@"; }
case "${1:-}" in
  create)
    mkdir -p "$(dirname "$KUBECONFIG")"
    k3d cluster create --config k8s/local/cluster.yaml --wait
    k3d kubeconfig get meteo-mlops > "$KUBECONFIG"
    chmod 600 "$KUBECONFIG"
    kubectl apply -f k8s/local/namespace.yaml
    ;;
  deploy)
    model="${2:-artifacts/baseline/model.joblib}"
    test -f "$model"
    docker build -t "$image" .
    k3d image import "$image" -c meteo-mlops
    kubectl apply -f k8s/local/config.yaml -f k8s/local/storage.yaml
    # Versioned immutable secrets preserve model bytes for Deployment rollback.
    version=$(shasum -a 256 "$model" | cut -c1-12)
    secret="inference-model-$version"
    kubectl -n meteo-mlops create secret generic "$secret" --from-file="model.joblib=$model" --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n meteo-mlops patch secret "$secret" --type=merge -p '{"immutable":true}'
    sed "s/secretName: inference-model$/secretName: $secret/" k8s/local/inference.yaml | kubectl apply -f -
    kubectl apply -f k8s/local/mlflow.yaml -f k8s/local/training-cronjob.yaml
    kubectl -n meteo-mlops rollout status deployment/mlflow --timeout=300s
    kubectl -n meteo-mlops rollout status deployment/inference --timeout=300s
    ;;
  train)
    kubectl -n meteo-mlops create job "training-$(date +%s)" --from=cronjob/training
    ;;
  destroy)
    k3d cluster delete meteo-mlops
    rm -f "$KUBECONFIG"
    ;;
  *) echo 'Usage: scripts/k3d-local.sh {create|deploy [model.joblib]|train|destroy}' >&2; exit 2 ;;
esac
