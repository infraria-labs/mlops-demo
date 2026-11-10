#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
state="$PWD/.kfp-lab"
cluster=meteo-kfp-lab
mkdir -p "$state"
export KUBECONFIG="$state/kubeconfig"
kubectl() { command kubectl --context k3d-meteo-kfp-lab "$@"; }
case "${1:-}" in
  create)
    k3d cluster create "$cluster" --image rancher/k3s:v1.33.9-k3s1 \
      --kubeconfig-update-default=false --kubeconfig-switch-context=false --wait
    k3d kubeconfig get "$cluster" > "$KUBECONFIG"
    chmod 600 "$KUBECONFIG"
    ;;
  install)
    test -s "$KUBECONFIG"
    test -d "$state/pipelines" || git clone --depth 1 --branch 2.17.0 https://github.com/kubeflow/pipelines.git "$state/pipelines"
    test -d "$state/argo" || git clone --depth 1 --branch v4.0.5 https://github.com/argoproj/argo-workflows.git "$state/argo"
    python3 experiments/kubeflow/render.py "$state/pipelines" "$state/argo"
    kubectl apply --server-side -k "$state/pipelines/manifests/kustomize/cluster-scoped-resources"
    kubectl apply -k experiments/kubeflow/install
    kubectl -n kubeflow patch configmap workflow-controller-configmap --type=merge \
      --patch-file experiments/kubeflow/retry-policy.json
    ;;
  preload) bash experiments/kubeflow/preload.sh ;;
  build)
    if [[ "${2:-}" != "--reuse-training" ]]; then
      docker build --target training -t meteo-kfp-training:v1 .
    fi
    context=$(mktemp -d)
    trap 'rm -rf "$context"' EXIT
    cp experiments/kubeflow/{Dockerfile,worker.py} "$context/"
    cp -R data/raw "$context/snapshot"
    docker build -t meteo-kfp-lab:v1 "$context"
    mkdir -p artifacts/kfp-lab
    docker image inspect meteo-kfp-lab:v1 --format '{"id":"{{.Id}}","architecture":"{{.Architecture}}"}' > artifacts/kfp-lab/image.json
    arch=$(docker image inspect meteo-kfp-lab:v1 --format '{{.Architecture}}')
    docker image save --platform "linux/$arch" -o "$state/training.tar" meteo-kfp-lab:v1
    k3d image import "$state/training.tar" -c "$cluster"
    rm -f "$state/training.tar"
    kubectl apply -f experiments/kubeflow/mlflow.yaml
    kubectl -n kubeflow rollout status deployment/lab-mlflow --timeout=300s
    ;;
  sdk)
    uv venv "$state/sdk"
    uv pip install --python "$state/sdk/bin/python" -r experiments/kubeflow/requirements.txt
    ;;
  compile) "$state/sdk/bin/python" experiments/kubeflow/pipeline.py ;;
  run) "$state/sdk/bin/python" experiments/kubeflow/run.py "${@:2}" ;;
  ui) kubectl -n kubeflow port-forward --address 127.0.0.1 service/ml-pipeline-ui 8081:80 ;;
  api) kubectl -n kubeflow port-forward --address 127.0.0.1 service/ml-pipeline 8888:8888 ;;
  mlflow) kubectl -n kubeflow port-forward --address 127.0.0.1 service/lab-mlflow 5001:5000 ;;
  status) kubectl -n kubeflow get pods,pvc,workflows ;;
  stop) k3d cluster stop "$cluster" ;;
  start) k3d cluster start "$cluster" ;;
  destroy)
    k3d cluster delete "$cluster"
    rm -f "$KUBECONFIG"
    ;;
  *) echo 'Usage: experiments/kubeflow/lab.sh {create|install|preload|build|sdk|compile|run|ui|api|mlflow|status|stop|start|destroy}' >&2; exit 2 ;;
esac
