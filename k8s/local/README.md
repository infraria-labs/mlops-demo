# Plain manifest map

Apply these in order using the isolated kubeconfig and the helper in `scripts/k3d-local.sh`:

| File | Primitive / purpose |
| --- | --- |
| `cluster.yaml` | k3d config, pinned k3s, loopback API |
| `namespace.yaml` | lab namespace |
| `config.yaml` | runtime environment and training JSON ConfigMaps |
| `storage.yaml` | MLflow and training local-path PVCs |
| `mlflow.yaml` | single SQLite server Deployment and ClusterIP Service |
| `inference.yaml` | model-consuming Deployment and ClusterIP Service |
| `training-job.yaml` | one-shot training Job |
| `training-cronjob.yaml` | initially suspended weekly training |

`inference.yaml` uses the placeholder Secret name `inference-model`; the deploy helper creates an immutable checksum-named Secret from a trusted local model and substitutes that reference before applying. Do not apply the placeholder manifest directly unless you have explicitly created the corresponding Secret.

The Job and CronJob run the same command, with identical configuration, resource limits and volume mounts. Raw manifests deliberately duplicate the pod spec so the Kubernetes primitives remain visible. See [the full lab](../../docs/kubernetes-local.md) for commands and verification.
