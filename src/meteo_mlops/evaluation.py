"""Matched-row forecast verification and compact evaluation tables."""
import numpy as np

def metrics(observed, predicted):
    observed, predicted = np.asarray(observed), np.asarray(predicted)
    if observed.ndim != 1 or observed.shape != predicted.shape or not observed.size:
        raise ValueError("Metrics require nonempty, equally shaped 1-D arrays")
    if not np.isfinite(observed).all() or not np.isfinite(predicted).all():
        raise ValueError("Metrics require finite values")
    error = predicted - observed
    return {"rmse_c": float(np.sqrt(np.mean(error**2))), "mae_c": float(np.mean(abs(error))), "bias_c": float(np.mean(error))}


def evaluate(parts, predictions, selected):
    report = {}
    for name, part in parts.items():
        scores = {method: metrics(part.observed_c, values) for method, values in predictions[name].items()}
        raw_rmse = scores["raw"]["rmse_c"]
        report[name] = {"n": len(part), "start": part.valid_time.min().isoformat(),
                        "end": part.valid_time.max().isoformat(), **scores,
                        "rmse_skill": {method: 1 - score["rmse_c"] / raw_rmse if raw_rmse else None
                                       for method, score in scores.items()}, "selected_model": selected}
    return report


def write_table(report, path):
    rows = ["# Evaluation", "", f"Selected using validation RMSE: **{report['selected_model']}**.", "",
            "All rows use the same observations at the fixed **24-hour forecast offset**.", "",
            "| Split | Method | Hours | RMSE °C | MAE °C | Bias °C | RMSE skill |",
            "|---|---|---:|---:|---:|---:|---:|"]
    for name, part in report["splits"].items():
        for method in ("raw", "mean_bias_correction", "ridge", "xgboost"):
            score, skill = part[method], part["rmse_skill"][method]
            skill_text = f"{skill:.1%}" if skill is not None else "undefined"
            rows.append(f"| {name} | {method} | {part['n']} | {score['rmse_c']:.3f} | {score['mae_c']:.3f} | {score['bias_c']:+.3f} | {skill_text} |")
    path.write_text("\n".join(rows) + "\n")
