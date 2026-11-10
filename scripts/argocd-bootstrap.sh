#!/usr/bin/env bash
# Install the pinned controller on an explicitly selected demo cluster.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${KUBECONFIG:?Set KUBECONFIG explicitly to the demo cluster}"
test -f "$KUBECONFIG"
kubectl get nodes
kubectl -n meteo-mlops get deployment inference mlflow
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply --server-side -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/v3.5.4/manifests/install.yaml
kubectl -n argocd rollout status deployment/argocd-server --timeout=600s
kubectl -n argocd rollout status deployment/argocd-repo-server --timeout=600s
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=600s
# Keep TLS and login enabled. The default ClusterIP UI is accessed by port-forward.
echo 'Argo CD installed. Configure repository read access before registering the Application.'
