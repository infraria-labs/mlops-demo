import pytest


@pytest.fixture(autouse=True)
def isolated_tracking(tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'mlflow.db'}")
    monkeypatch.setenv("MLFLOW_ARTIFACT_ROOT", (tmp_path / "mlflow-artifacts").as_uri())
