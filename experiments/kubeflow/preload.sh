#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
state="$PWD/.kfp-lab"
mkdir -p "$state"
case "$(docker info --format '{{.Architecture}}')" in
  aarch64|arm64) platform=linux/arm64 ;;
  x86_64|amd64) platform=linux/amd64 ;;
  *) echo 'Unsupported Docker architecture' >&2; exit 1 ;;
esac
images=()
while IFS= read -r image; do
  docker pull --platform "$platform" "$image"
  images+=("$image")
done < experiments/kubeflow/images.txt
# Select the loaded platform, avoiding references to unpulled manifests.
docker image save --platform "$platform" -o "$state/control-plane.tar" "${images[@]}"
k3d image import "$state/control-plane.tar" -c meteo-kfp-lab
metadata=gcr.io/tfx-oss-public/ml_metadata_store_server:1.14.0
docker pull --platform linux/amd64 "$metadata"
docker image save --platform linux/amd64 -o "$state/metadata.tar" "$metadata"
k3d image import "$state/metadata.tar" -c meteo-kfp-lab
rm -f "$state/control-plane.tar" "$state/metadata.tar"
