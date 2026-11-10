#!/usr/bin/env bash
# Temporary EKS lab. All Kubernetes commands force the dedicated kubeconfig/context.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
mkdir -p .eks
tf() { terraform -chdir=infra/eks "$@"; }
region=$(tf output -raw region)
cluster=$(tf output -raw cluster_name)
config="$PWD/.eks/kubeconfig"
context="eks-$cluster"
k() { kubectl --kubeconfig "$config" --context "$context" "$@"; }
h() { helm --kubeconfig "$config" --kube-context "$context" -n meteo-mlops "$@"; }
case "${1:?Choose kubeconfig, deploy, monitoring, status, clean-workloads}" in
  kubeconfig)
    aws eks update-kubeconfig --region "$region" --name "$cluster" --kubeconfig "$config" --alias "$context"
    k get nodes
    ;;
  deploy)
    model="${2:?Supply trusted model.joblib}"
    test -f "$model"
    # Cluster identity is checked before any mutation; never copy k3s Secrets.
    k get nodes
    k apply -f k8s/eks/storage-class.yaml
    k create namespace meteo-mlops --dry-run=client -o yaml | k apply -f -
    digest=$(shasum -a 256 "$model" | awk '{print $1}')
    secret="inference-model-${digest:0:12}"
    if ! k -n meteo-mlops get secret "$secret" >/dev/null 2>&1; then
      k -n meteo-mlops create secret generic "$secret" --from-file="model.joblib=$model"
      k -n meteo-mlops patch secret "$secret" --type=merge -p '{"immutable":true}'
    fi
    # Local generated values are private. Commit reviewed desired state separately for Argo.
    printf 'mlflow:\n  artifactDestination: %s\nmodelSecret: %s\nregion: %s\n' \
      "$(tf output -raw artifact_destination)" "$secret" "$region" > .eks/runtime-values.yaml
    h upgrade --install meteo-mlops charts/meteo-mlops -f gitops/eks/values.yaml -f .eks/runtime-values.yaml \
      --set gitops.enabled=false --wait --timeout 10m --history-max 5
    k -n meteo-mlops rollout status deployment/inference --timeout=300s
    k -n meteo-mlops rollout status deployment/mlflow --timeout=300s
    ;;
  monitoring)
    k get nodes
    if ! k -n meteo-mlops get secret monitoring-grafana >/dev/null 2>&1; then
      credential=$(mktemp)
      trap 'rm -f "$credential"' EXIT
      openssl rand -hex 24 | tr -d '\n' > "$credential"
      k -n meteo-mlops create secret generic monitoring-grafana --from-file="password=$credential"
    fi
    h upgrade --install monitoring charts/meteo-monitoring --set storageClassName=eks-gp3 --wait --timeout 10m --history-max 5
    ;;
  status)
    k get nodes
    k -n meteo-mlops get pods,pvc
    k get pv
    aws eks list-addons --region "$region" --cluster-name "$cluster"
    ;;
  clean-workloads)
    # Authorized only for disposable data in this dedicated EKS environment.
    k get nodes
    if [[ -n "$(k get crd applications.argoproj.io --ignore-not-found -o name)" ]]; then
      k -n argocd delete application meteo-mlops --ignore-not-found
    fi
    k delete namespace argocd --ignore-not-found --wait=true --timeout=300s
    h uninstall monitoring --ignore-not-found
    h uninstall meteo-mlops --ignore-not-found
    k -n meteo-mlops delete jobs --all --wait=true
    k -n meteo-mlops delete pvc --all --wait=true --timeout=300s
    # Wait for CSI Delete reclaim to remove every exercise PV before deleting its IAM role/node.
    for attempt in {1..60}; do
      remaining=$(k get pv -o jsonpath='{range .items[?(@.spec.storageClassName=="eks-gp3")]}{.metadata.name}{"\n"}{end}')
      if [[ -z "$remaining" ]]; then break; fi
      if [[ "$attempt" == 60 ]]; then echo "CSI PV deletion did not finish; retain cluster and inspect events." >&2; exit 1; fi
      sleep 5
    done
    k delete namespace meteo-mlops --ignore-not-found --wait=true --timeout=300s
    echo 'Workloads/PVs removed. Review terraform -chdir=infra/eks plan -destroy, then destroy this state only.'
    ;;
  *) echo 'Unknown action' >&2; exit 2 ;;
esac
