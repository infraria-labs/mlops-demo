"""Build and smoke-test a container using a trusted model, or a deterministic CI fixture."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor

from meteo_mlops.features import FEATURES
from meteo_mlops.training import predict


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--image", default="meteo-inference:smoke")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    if not args.skip_build:
        subprocess.run(["docker", "build", "-t", args.image, "."], check=True)
    with tempfile.TemporaryDirectory() as directory:
        model_path = args.model.resolve() if args.model else Path(directory) / "model.joblib"
        if not args.model:
            model = DummyRegressor(strategy="constant", constant=2.5).fit(np.zeros((2, 5)), [0, 0])
            joblib.dump(dict(schema_version=2, features=FEATURES, station="NZWN", horizon_hours=24,
                            forecast_model="gfs_global", model_name="mean_bias_correction", model=model), model_path)
        inputs = [{"valid_time": "2026-01-01T12:00:00Z", "forecast_c": 18.0}]
        expected = predict(joblib.load(model_path), pd.DataFrame(inputs)).tolist()
        container = docker("run", "-d", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                           "-p", "127.0.0.1::8000", "--mount", f"type=bind,src={model_path},dst=/models/model.joblib,readonly", args.image)
        try:
            port = docker("port", container, "8000/tcp").rsplit(":", 1)[1]
            base = f"http://127.0.0.1:{port}"
            def request(path, payload=None):
                data = json.dumps(payload).encode() if payload is not None else None
                with urlopen(Request(base + path, data=data, headers={"Content-Type": "application/json"}), timeout=3) as response:
                    return json.load(response)
            for attempt in range(120):
                try:
                    metadata = request("/ready")
                    break
                except (URLError, ConnectionError, TimeoutError):
                    if docker("inspect", "-f", "{{.State.Running}}", container) != "true":
                        raise RuntimeError("Container exited during startup")
                    time.sleep(0.5)
            else:
                raise RuntimeError("Container readiness timed out")
            assert request("/health") == {"status": "ok"}
            assert metadata["model_version"] == hashlib.sha256(model_path.read_bytes()).hexdigest()
            result = request("/predict", {"forecasts": inputs})
            np.testing.assert_allclose([row["corrected_c"] for row in result["forecasts"]], expected, rtol=1e-6)
            try:
                request("/predict", {"forecasts": []})
            except HTTPError as error:
                assert error.code == 422
            else:
                raise AssertionError("Invalid request accepted")
            assert docker("exec", container, "id", "-u") == "10001"
            for _ in range(30):
                if docker("inspect", "-f", "{{.State.Health.Status}}", container) == "healthy":
                    break
                time.sleep(0.5)
            else:
                raise AssertionError("Docker health check failed")
            print(json.dumps({"status": "passed", "model": str(model_path) if args.model else "CI fixture", **metadata}))
        finally:
            print(docker("logs", container))
            docker("rm", "-f", container)


if __name__ == "__main__":
    main()
