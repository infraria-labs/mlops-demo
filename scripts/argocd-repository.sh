#!/usr/bin/env bash
# Install repository-scoped read-only credentials from local files, never logs/Git.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${KUBECONFIG:?Set KUBECONFIG explicitly to the demo cluster}"
test -f "$KUBECONFIG"
kubectl -n argocd get deployment argocd-repo-server >/dev/null
umask 077
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
args=(--from-literal=type=git --from-literal=url=https://github.com/infraria-labs/mlops-demo.git)
case "${1:-}" in
  token)
    token_file="${2:?Provide a file containing a repository-scoped Contents-read token}"
    python3 - "$token_file" "$work/password" <<'PY'
from pathlib import Path
import sys
token = Path(sys.argv[1]).read_text().strip()
if not token or '\n' in token:
    raise ValueError('Expected one nonempty token')
Path(sys.argv[2]).write_text(token)
PY
    args+=(--from-literal=username=x-access-token --from-file="password=$work/password")
    ;;
  app)
    app_id="${2:?Provide GitHub App ID}"
    installation_id="${3:?Provide repository installation ID}"
    private_key_file="${4:?Provide local GitHub App private key file}"
    test -s "$private_key_file"
    [[ "$app_id" =~ ^[0-9]+$ && "$installation_id" =~ ^[0-9]+$ ]]
    args+=(--from-literal="githubAppID=$app_id" --from-literal="githubAppInstallationID=$installation_id"
           --from-file="githubAppPrivateKey=$private_key_file")
    ;;
  *) echo 'Usage: argocd-repository.sh token TOKEN_FILE | app APP_ID INSTALLATION_ID PRIVATE_KEY_FILE' >&2; exit 2 ;;
esac
kubectl -n argocd create secret generic meteo-mlops-repository "${args[@]}" \
  --dry-run=client -o yaml > "$work/secret.yaml"
# Avoid kubectl's last-applied annotation duplicating credential material.
kubectl apply --server-side --field-manager=argocd-repository-bootstrap -f "$work/secret.yaml"
kubectl -n argocd label secret meteo-mlops-repository argocd.argoproj.io/secret-type=repository --overwrite
echo 'Repository credential installed. Verify connection in Argo CD; temporary files removed on exit.'
