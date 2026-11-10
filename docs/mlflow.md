# Local MLflow workflow

Purpose: inspect runs and retrieve a specific model bundle. Prerequisite: the locked Python environment and a selected tracking store. Continue to [retraining and controlled promotion](retraining.md) before changing champion or serving.

From the repository root, install the locked environment and start the loopback-only UI:

```sh
uv sync --locked
uv run --locked meteo-tracking server
# Another terminal:
uv run --locked meteo-baseline run
```

Open http://127.0.0.1:5000. SQLite metadata/registry lives in `.mlflow/mlflow.db`; local artifacts live in `.mlflow/artifacts/`. Both are ignored by Git. Back up both together; deleting them loses run history and registry versions. SQLite is for one local developer; for the Kubernetes tracking server, use its port-forwarded HTTP endpoint instead. The UI and CLI share absolute artifact paths on this machine. Optionally set `MLFLOW_TRACKING_URI=http://127.0.0.1:5000` for client/server access. Without it, training writes directly to the same SQLite database and does not need a running server. A tracking failure fails the command; do not interpret untracked output files as a successful tracked run.

Experiment naming is `meteo/<station>/<forecast model>/<target>/<horizon>`; this baseline uses `meteo/NZWN/gfs_global/temperature_2m/24h`. Every training invocation creates one run. Tags include `git_sha`, `git_dirty`, `data_version` (SHA-256 of the sorted input-hash manifest), `model_type`, station, forecast model and horizon. Commit source changes for reproducibility: a dirty SHA alone cannot reconstruct local edits. The metadata artifact also records source hashes, Python/package versions and the lockfile hash. Preserve the corresponding raw data bytes and checkout, then rerun with the logged config; archive providers can change.

Parameters capture all candidate hyperparameters, seed, feature list and resolved split/config settings. Metrics include each method's RMSE, MAE, bias and RMSE skill on train/validation/test, split counts and every candidate validation RMSE. Artifacts include the selected and reference joblib models, metadata, configuration, evaluation table, metrics, holdout predictions and a holdout RMSE plot. `artifacts/baseline/tracking.json` records the run ID and registry version. The test set evaluates selection; it never chooses the model.

## Registry and manual promotion

`nzwn-gfs-temperature-24h` contains versioned **artifact bundles**, with the exact `model.joblib` expected by the inference API. These are not MLflow pyfunc models; retrieve the bundle rather than using `mlflow.pyfunc.load_model`. Each completed training invocation registers its validation-selected model, including a raw or mean-correction winner. Registration never changes aliases.

`candidate` names a version under review. `champion` names the production-approved version. Production means a finished run whose held-out metrics, input provenance, feature contract and API smoke results have been reviewed and explicitly approved. No automatic promotion and no "latest" lookup. Compare candidate performance against the current champion on identical held-out rows, including raw baseline skill; reject regressions or document the tradeoff. Registration alone is not approval.

```sh
uv run --locked meteo-tracking alias candidate 1
# After reviewing evidence and inference compatibility:
uv run --locked meteo-lifecycle evaluate 1
uv run --locked meteo-lifecycle promote 1
uv run --locked meteo-tracking retrieve models:/nzwn-gfs-temperature-24h/1 --output artifacts/release-1
# The command prints the downloaded bundle directory; use its model.joblib:
MODEL_PATH=artifacts/release-1/model.joblib uv run --locked uvicorn meteo_mlops.api:app --port 8000
# Retrieving the original run yields the same bundle:
uv run --locked meteo-tracking retrieve runs:/RUN_ID/bundle --output artifacts/run-copy
```

Aliases are mutable pointers. Resolve `models:/nzwn-gfs-temperature-24h@champion` once when preparing a release, record the numeric version and run ID, retrieve that explicit version, and deploy the local artifact. The service loads it once at startup and returns its SHA-256 as `model_version`; it does not query a moving alias during requests. Validate `/ready` and `/predict` with the existing container smoke script before release. Load only trusted bundles: joblib can execute code.

Keep the previous approved version and artifact checksum in the release record. Rollback uses `meteo-lifecycle rollback VERSION` for a previously approved, finished numeric version, retrieves its bundle, and redeploys inference with the previous serving pin or artifact. Changing an alias alone does not update a running service. Preserve registry versions and artifact bytes required for rollback.

MLflow reference: [tracking server](https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/) and [registry aliases](https://www.mlflow.org/docs/latest/ml/model-registry/workflow/).

Champion alias changes now recompute the evaluation gate; use [the lifecycle workflow](retraining.md) for approved promotion, version-pinned serving and rollback.
