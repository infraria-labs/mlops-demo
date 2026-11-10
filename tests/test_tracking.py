import sys

import mlflow
import pytest

from meteo_mlops.tracking import MODEL, configure, main


def test_manual_promotion_rollback_and_failed_run_rejected(monkeypatch):
    client = configure()
    client.create_registered_model(MODEL)
    versions = []
    for _ in range(2):
        with mlflow.start_run() as run:
            mlflow.log_dict({"fixture": True}, "bundle/metadata.json")
            source = run.info.artifact_uri + "/bundle"
        versions.append(client.create_model_version(MODEL, source, run.info.run_id).version)
    monkeypatch.setattr(sys, "argv", ["meteo-tracking", "alias", "candidate", str(versions[1])])
    main()
    assert client.get_model_version_by_alias(MODEL, "candidate").version == versions[1]
    with pytest.raises(RuntimeError):
        with mlflow.start_run() as failed:
            raise RuntimeError("training failed")
    rejected = client.create_model_version(MODEL, failed.info.artifact_uri, failed.info.run_id)
    monkeypatch.setattr(sys, "argv", ["meteo-tracking", "alias", "champion", str(rejected.version)])
    with pytest.raises(ValueError, match="finished"):
        main()
    assert client.get_model_version_by_alias(MODEL, "candidate").version == versions[1]


def test_git_metadata_without_git_in_runtime_image(monkeypatch):
    from meteo_mlops import tracking

    def missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(tracking.subprocess, "run", missing_git)
    assert tracking.git_metadata() == {"git_sha": "unknown", "git_dirty": "unknown"}
