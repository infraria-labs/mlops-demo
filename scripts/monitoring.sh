#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
target="${1:?Specify local or aws}"
action="${2:?Choose an action}"
case "$target" in
  local) config="$PWD/.k3d/kubeconfig"; context=k3d-meteo-mlops ;;
  aws) config="$PWD/.aws-k3s/kubeconfig"; context=$(kubectl --kubeconfig "$config" config current-context) ;;
  *) echo 'Target must be local or aws' >&2; exit 2 ;;
esac
k() { kubectl --kubeconfig "$config" --context "$context" -n meteo-mlops "$@"; }
h() { helm --kubeconfig "$config" --kube-context "$context" -n meteo-mlops "$@"; }
k get nodes
case "$action" in
  deploy)
    if ! k get secret monitoring-grafana >/dev/null 2>&1; then
      credential=$(mktemp)
      trap 'rm -f "$credential"' EXIT
      chmod 600 "$credential"
      openssl rand -hex 24 | tr -d '\n' > "$credential"
      k create secret generic monitoring-grafana --from-file="password=$credential"
    fi
    h lint charts/meteo-monitoring --strict
    h upgrade --install monitoring charts/meteo-monitoring --atomic --wait --timeout 5m --history-max 5
    ;;
  status) h status monitoring; k get pods,pvc; k top pods || true ;;
  grafana) k port-forward --address 127.0.0.1 service/monitoring-grafana 3000:3000 ;;
  prometheus) k port-forward --address 127.0.0.1 service/monitoring-prometheus 9090:9090 ;;
  remove) h uninstall monitoring ;;
  *) echo 'Action must be deploy, status, grafana, prometheus or remove' >&2; exit 2 ;;
esac
