"""Evaluate immutable registry versions, explicitly promote, and pin serving bundles."""
import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path
import tempfile

import joblib
import mlflow
import pandas as pd
from mlflow.exceptions import MlflowException

from .evaluation import metrics
from .tracking import MODEL, configure
from .training import predict


def gate(candidate, reference, improvement=0.02, tolerance=0.0):
    if not math.isfinite(improvement) or improvement <= 0 or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Thresholds require positive improvement and nonnegative tolerance")
    for scores in (candidate, reference):
        if any(not math.isfinite(scores[k]) for k in ("rmse_c", "mae_c", "bias_c")) or any(scores[k] < 0 for k in ("rmse_c", "mae_c")):
            return {"approved": False, "reason": "invalid metrics"}
    approved = (candidate['rmse_c'] <= reference['rmse_c'] - improvement
                and candidate['mae_c'] <= reference['mae_c'] + tolerance
                and abs(candidate['bias_c']) <= abs(reference['bias_c']) + tolerance)
    return {"approved": approved, "reason": "thresholds satisfied" if approved else "regression or insufficient improvement"}


def champion(client):
    try:
        return client.get_registered_model(MODEL).aliases.get('champion')
    except MlflowException as error:
        if error.error_code != 'RESOURCE_DOES_NOT_EXIST':
            raise
        return None


def bundle(client, version, output):
    model = client.get_model_version(MODEL, str(version))
    if client.get_run(model.run_id).info.status != 'FINISHED':
        raise ValueError('Only finished runs are eligible')
    output.mkdir(parents=True, exist_ok=True)
    return Path(mlflow.artifacts.download_artifacts(artifact_uri=model.source, dst_path=str(output.resolve())))


def evaluate_candidate(client, version, improvement=0.02, tolerance=0.0):
    current = champion(client)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        candidate = bundle(client, version, root / 'candidate')
        frame = pd.read_csv(candidate / 'test-predictions.csv')
        frame['valid_time'] = pd.to_datetime(frame.valid_time, utc=True)
        model = joblib.load(candidate / 'model.joblib')
        start, end = [pd.to_datetime(t, utc=True) for t in model['config']['splits']['test']]
        if (frame.valid_time.isna().any() or frame.valid_time.duplicated().any()
                or frame.valid_time.min() < start or frame.valid_time.max() >= end):
            raise ValueError('Invalid verification window')
        if len(frame) < model['config']['minimum_split_hours']:
            raise ValueError('Insufficient verification rows')
        candidate_scores = metrics(frame.observed_c, predict(model, frame))
        reference_scores = metrics(frame.observed_c, frame.forecast_c)
        if current:
            production = bundle(client, current, root / 'champion')
            production_model = joblib.load(production / 'model.joblib')
            # Never evaluate a champion on observations it trained/selected on.
            end = pd.to_datetime(production_model['config']['splits']['validation'][1], utc=True)
            if frame.valid_time.min() < end + pd.Timedelta(hours=24, minutes=10):
                raise ValueError('Verification window overlaps champion training/validation embargo')
            reference_scores = metrics(frame.observed_c, predict(production_model, frame))
        decision = {**gate(candidate_scores, reference_scores, improvement, tolerance),
                    'version': str(version), 'champion': current, 'candidate_metrics': candidate_scores,
                    'reference_metrics': reference_scores, 'improvement_c': improvement,
                    'tolerance_c': tolerance, 'hours': len(frame),
                    'start': frame.valid_time.min().isoformat(), 'end': frame.valid_time.max().isoformat()}
        registered = client.get_model_version(MODEL, str(version))
        with mlflow.start_run(run_id=registered.run_id):
            mlflow.log_dict(decision, 'lifecycle/decision.json')
            mlflow.set_tags({'promotion.approved': str(decision['approved']).lower(),
                             'promotion.reason': decision['reason'], 'promotion.reference': current or 'raw'})
        return decision


def promote(client, version):
    # Recompute the decision against the current champion at promotion time.
    decision = evaluate_candidate(client, version)
    if not decision['approved']:
        raise ValueError(decision['reason'])
    if champion(client) != decision['champion']:
        raise ValueError('Champion changed during evaluation; retry')
    if decision['champion']:
        client.set_registered_model_alias(MODEL, 'previous', decision['champion'])
    client.set_model_version_tag(MODEL, str(version), 'promotion.approved', 'true')
    client.set_registered_model_alias(MODEL, 'champion', str(version))
    return decision


def materialize(client, version, output):
    if not str(version).isdigit():
        raise ValueError('Serving requires an immutable numeric version')
    with tempfile.TemporaryDirectory() as directory:
        downloaded = bundle(client, version, Path(directory))
        output.mkdir(parents=True, exist_ok=True)
        for name in ('model.joblib',):
            shutil.copy2(downloaded / name, output / name)
    path = output
    digest = hashlib.sha256((path / 'model.joblib').read_bytes()).hexdigest()
    (path / 'registry.json').write_text(json.dumps({'name': MODEL, 'version': str(version), 'sha256': digest}) + '\n')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['evaluate', 'promote', 'rollback', 'materialize'])
    parser.add_argument('version', type=int)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    client = configure()
    if args.command == 'evaluate':
        print(json.dumps(evaluate_candidate(client, args.version), indent=2))
    elif args.command == 'promote':
        print(json.dumps(promote(client, args.version), indent=2))
    elif args.command == 'rollback':
        version = client.get_model_version(MODEL, str(args.version))
        if version.tags.get('promotion.approved') != 'true' or client.get_run(version.run_id).info.status != 'FINISHED':
            raise ValueError('Rollback requires a previously approved finished version')
        previous = champion(client)
        with mlflow.start_run(run_name='model-rollback'):
            mlflow.log_dict({'from': previous, 'to': str(args.version)}, 'lifecycle/rollback.json')
            client.set_registered_model_alias(MODEL, 'champion', str(args.version))
    else:
        if args.output is None:
            parser.error('--output is required')
        print(materialize(client, args.version, args.output))


if __name__ == '__main__':
    main()
