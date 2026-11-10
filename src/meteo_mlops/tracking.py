"""Local experiment tracking and explicit, manual artifact lifecycle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

import mlflow
from mlflow import MlflowClient

EXPERIMENT = "meteo/NZWN/gfs_global/temperature_2m/24h"
MODEL = "nzwn-gfs-temperature-24h"


def configure():
    state = Path(".mlflow").resolve()
    state.mkdir(exist_ok=True)
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", f"sqlite:///{state / 'mlflow.db'}"))
    client = MlflowClient()
    experiment = client.get_experiment_by_name(EXPERIMENT)
    if experiment is None:
        client.create_experiment(EXPERIMENT, artifact_location=os.environ.get("MLFLOW_ARTIFACT_ROOT", (state / "artifacts").as_uri()))
    mlflow.set_experiment(EXPERIMENT)
    return client


def git_metadata():
    def git(*args):
        try:
            result = subprocess.run(["git", *args], capture_output=True, text=True)
        except FileNotFoundError:
            return "unknown"
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    status = git("status", "--porcelain")
    return {"git_sha": os.environ.get("TRAINING_GIT_SHA", git("rev-parse", "HEAD")),
            "git_dirty": "unknown" if status == "unknown" else str(bool(status)).lower()}


def log_result(output, report):
    metadata = json.loads((output / "metadata.json").read_text())
    data_version = hashlib.sha256(json.dumps(metadata["data_sha256"], sort_keys=True).encode()).hexdigest()
    mlflow.set_tags({**git_metadata(), "data_version": data_version, "model_type": report["selected_model"],
                     "station": "NZWN", "forecast_model": "gfs_global", "horizon_hours": "24",
                     "training_image": os.environ.get("TRAINING_IMAGE", "local")})
    mlflow.log_dict(metadata["config"], "config.json")
    for source in sorted(Path(__file__).parent.glob("*.py")):
        mlflow.log_artifact(str(source), "source")
    if Path("uv.lock").exists():
        mlflow.log_artifact("uv.lock", "source")
    mlflow.log_params({"seed": metadata["config"]["seed"], "features": json.dumps(metadata["features"]),
                       "config": json.dumps(metadata["config"], sort_keys=True)})
    for split, scores in report["splits"].items():
        mlflow.log_metric(f"{split}.n", scores["n"])
        for method in ("raw", "mean_bias_correction", "ridge", "xgboost"):
            mlflow.log_metrics({f"{split}.{method}.{key}": value for key, value in scores[method].items()})
            if scores["rmse_skill"][method] is not None:
                mlflow.log_metric(f"{split}.{method}.rmse_skill", scores["rmse_skill"][method])
    for method, candidates in report["validation_candidates"].items():
        for index, candidate in enumerate(candidates):
            mlflow.log_params({f"{method}.{index}.{key}": value for key, value in candidate["parameters"].items()})
            mlflow.log_metric(f"validation.{method}.candidate_{index}.rmse_c", candidate["rmse_c"])
    # A portable trusted joblib bundle, consumed by the existing inference API.
    # Only the explicit files from this run are logged (never stale output files).
    for filename in ("model.joblib", "ridge.joblib", "xgboost.joblib", "metadata.json", "metrics.json",
                     "evaluation.md", "test-predictions.csv"):
        mlflow.log_artifact(str(output / filename), "bundle")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    methods = ["raw", "mean_bias_correction", "ridge", "xgboost"]
    ax.bar(methods, [report["splits"]["test"][m]["rmse_c"] for m in methods])
    ax.set_ylabel("Holdout RMSE (°C)")
    ax.tick_params(axis="x", labelrotation=20)
    fig.tight_layout()
    mlflow.log_figure(fig, "evaluation/holdout-rmse.png")
    plt.close(fig)
    run_id = mlflow.active_run().info.run_id
    client = MlflowClient()
    from mlflow.exceptions import MlflowException
    try:
        client.create_registered_model(MODEL)
    except MlflowException as error:
        if error.error_code != "RESOURCE_ALREADY_EXISTS":
            raise
    source = mlflow.active_run().info.artifact_uri.rstrip("/") + "/bundle"
    registered = client.create_model_version(MODEL, source=source, run_id=run_id)
    client.set_model_version_tag(MODEL, registered.version, "selection", "validation_rmse")
    # Registration alone never moves champion.
    (output / "tracking.json").write_text(json.dumps({"run_id": run_id, "model_name": MODEL,
                                                   "model_version": registered.version}, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("server")
    serve.add_argument("--port", type=int, default=5000)
    promote = sub.add_parser("alias")
    promote.add_argument("alias", choices=["candidate", "champion"])
    promote.add_argument("version", type=int)
    retrieve = sub.add_parser("retrieve")
    retrieve.add_argument("uri", help="runs:/RUN/bundle or models:/NAME/VERSION (aliases resolve once)")
    retrieve.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "server":
        state = Path(".mlflow").resolve()
        state.mkdir(exist_ok=True)
        subprocess.run(["mlflow", "server", "--host", "127.0.0.1", "--port", str(args.port),
                        "--backend-store-uri", f"sqlite:///{state / 'mlflow.db'}", "--no-serve-artifacts",
                        "--default-artifact-root", (state / "artifacts").as_uri()], check=True)
        return
    client = configure()
    if args.command == "alias":
        model = client.get_model_version(MODEL, str(args.version))
        run = client.get_run(model.run_id)
        if run.info.status != "FINISHED":
            raise ValueError("Only finished training runs can be promoted")
        if args.alias == "champion":
            from .lifecycle import promote
            promote(client, args.version)
        else:
            client.set_registered_model_alias(MODEL, args.alias, str(args.version))
        print(f"{MODEL}@{args.alias} -> {args.version}")
    else:
        if not args.uri.startswith(("runs:/", "models:/")):
            parser.error("Use a runs:/ or models:/ URI")
        args.output.mkdir(parents=True, exist_ok=True)
        print(mlflow.artifacts.download_artifacts(artifact_uri=args.uri, dst_path=str(args.output.resolve())))


if __name__ == "__main__":
    main()
