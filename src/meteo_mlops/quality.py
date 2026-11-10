"""Explicit observation verification for the exact serving artifact; never promote."""
import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .evaluation import metrics
from .training import predict


def verify(model_path, observations, output):
    artifact = joblib.load(model_path)  # Trusted artifacts only, as in the serving API.
    frame = pd.read_csv(observations)
    frame['valid_time'] = pd.to_datetime(frame.valid_time, utc=True)
    if frame.valid_time.isna().any() or frame.valid_time.duplicated().any():
        raise ValueError('Verification timestamps must be unique and valid')
    frame = frame.sort_values('valid_time')
    if len(frame) < 168 or not np.isfinite(frame[['forecast_c', 'observed_c']].to_numpy()).all():
        raise ValueError('Verification requires at least 168 finite matched hours')
    if (frame.valid_time.diff().dropna() < pd.Timedelta(hours=1)).any():
        raise ValueError('Verification rows must be at least hourly')
    cutoff = pd.to_datetime(artifact['config']['splits']['validation'][1], utc=True)
    if frame.valid_time.min() < cutoff + pd.Timedelta(hours=24, minutes=10):
        raise ValueError('Verification overlaps model training/selection embargo')
    corrected = predict(artifact, frame)
    raw = metrics(frame.observed_c, frame.forecast_c)
    scores = metrics(frame.observed_c, corrected)
    for method, values in [('raw', frame.forecast_c.to_numpy()), ('corrected', corrected)]:
        target = raw if method == 'raw' else scores
        target['rolling_bias_c'] = metrics(frame.observed_c.tail(168), values[-168:])['bias_c']
        target['rmse_skill'] = 1 - target['rmse_c'] / raw['rmse_c'] if raw['rmse_c'] else None
    payload = {'sha256': hashlib.sha256(Path(model_path).read_bytes()).hexdigest(),
               'start': frame.valid_time.min().timestamp(), 'end': frame.valid_time.max().timestamp(),
               'samples': len(frame), 'rolling_samples': 168, 'quality': {'raw': raw, 'corrected': scores}}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, allow_nan=False, indent=2) + '\n')
    temporary.replace(output)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--observations', type=Path, required=True,
                        help='Trusted matched CSV: valid_time, forecast_c, observed_c for NZWN/GFS/24h; pressure models also require pressure_msl_hpa')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.model, args.observations, args.output)


if __name__ == '__main__':
    main()
