# Problem and evaluation contract

Purpose: understand the data, feature and evaluation contract before changing an experiment. Start with `uv run --locked meteo-baseline run`; inspect `artifacts/baseline/evaluation.md` and `metadata.json`. Continue to [measured results](results.md), then [MLflow tracking](mlflow.md).

## Target, site and horizon

Hourly station air temperature in °C at Wellington Airport (NZWN, requested coordinates −41.3272, 174.8053), a near-surface observational proxy for the model's 2 m temperature. One station keeps ingestion and interpretation small. Wellington's coastal terrain gives a plausible grid-to-station correction problem; site selection precedes evaluation and does not depend on test skill.

Forecasts are [Open-Meteo Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api) `gfs_global`, `temperature_2m_previous_day1`, UTC, Celsius. The provider documents day1 as the forecast predicted 24 hours before valid time. This is the provider's fixed-offset archive, not an independently reconstructed exact initialization/lead GRIB dataset. API interpolation, land-cell selection and default elevation adjustment are part of the raw baseline. Record returned grid coordinates/elevation in cached JSON. Do not claim these are untouched native GFS fields. No best-match model, current-run forecast or reanalysis is substituted.

Observations are independent routine METAR temperatures (`tmpc`, report type 3) from the [Iowa Environmental Mesonet ASOS archive](https://mesonet.agron.iastate.edu/request/download.phtml). Forecast data are not used as observation truth. Station air temperature is not an exact laboratory 2 m reference; METAR rounding, exposure and grid representativeness contribute to error. The archive is an as-available collection, not guaranteed research-grade QC.

## Alignment and quality

Fetch 2024-01-01 through 2025-12-31. Round observation timestamps to the nearest UTC hour and retain the closest routine report within 10 minutes (ties resolved by earlier observation time); omit unmatched hours. Preserve actual observation timestamps. Drop missing/non-numeric observations, temperatures outside −50 to 50 °C and missing forecasts. Reject duplicate forecast times, nonfinite data, overall coverage below 80%, or any split with fewer than 1,000 matched hours. All methods use identical paired rows. No forward filling, future weather features, or target interpolation.

## Temporal strategy

- Training: 2024-01-01 through 2024-12-29, a nearly full seasonal cycle.
- Validation: 2025-01-01 through 2025-06-28.
- Test: 2025-07-01 through 2025-12-31.

The two omitted days at each boundary exceed the 24-hour forecast offset plus observation matching tolerance. Scaler, residual model and mean bias are fitted on training only. Select ridge alpha from the declared grid 0.1, 1, 10, 100 and XGBoost parameters from the two declared configurations by validation RMSE; ties retain the first configuration in listed order. Also compare raw forecasts and mean-bias correction on validation to select the exported method. Keep training-only fits for test evaluation. Test data never choose features or parameters. Future changes to this design require a fresh holdout before a new claim of generalization.

## Baselines and first model

Measure raw forecast, training mean residual correction, standardized ridge regression and CPU histogram XGBoost residual regression. XGBoost uses a fixed seed, one thread, full row/column sampling and two modest candidates (100 depth-2 or 200 depth-3 trees, learning rate 0.05, minimum child weight 20). Inputs are raw forecast temperature, sine/cosine of UTC valid hour, and sine/cosine of fractional valid year (leap-year aware). All are available at prediction time. Predict the residual and add it to raw temperature. No observations enter prediction inputs.

Report RMSE, MAE, signed bias and RMSE skill on each split. Held-out improvement is evidence for this station and period only. A half-year test is seasonally unbalanced, hourly errors are correlated, model upgrades can create drift, and no statistical significance is claimed. Multi-year rolling-origin evaluation and daily-block bootstrap intervals are later work. Do not deploy merely because aggregate RMSE improves.

## Data provenance and usage

Raw responses and a request URL/SHA-256 manifest are cached locally and excluded from Git, alongside artifacts. Metrics include input hashes. Retain the cache when reproducing a run. Open-Meteo attribution: weather data by Open-Meteo and NOAA GFS; check the provider's [terms](https://open-meteo.com/en/terms) for commercial API usage before platform deployment. Observation source: Iowa Environmental Mesonet, routine METAR archive. No paid API or credentials are required by this local research workflow.

## Feature candidates and leakage decisions

Lead time is fixed at 24 hours; a constant lead-time input adds no information and a lead-time breakdown would duplicate the aggregate table. Hour and year harmonics express daily/seasonal bias without discontinuities. Raw forecast temperature captures temperature-dependent correction. Recent station bias is a plausible later feature but is deferred: it needs observation publication/availability timestamps, lagging relative to forecast issuance (valid time minus 24 hours), and a realistic missing-observation policy. Using recent observations relative to valid time would leak information unavailable at issuance. The current model needs no live observations at inference.

The original ridge test results were already inspected before this subissue adjustment. Added XGBoost comparisons on the same test set are exploratory; validation alone selects parameters and the exported method, but a fresh future holdout is required before claiming independently confirmed model-selection gains.

## Subissue coverage in order

1. **the implementation — Select dataset, target and evaluation design:** this note defines target, station, fixed lead, data sources, feature decisions, temporal boundaries, leakage risks and metrics.
2. **the implementation — Build reproducible training pipeline:** uv project/lock, separate data/features/training/evaluation modules, deterministic preprocessing, ridge and XGBoost candidates, JSON config and CLI, serialized/reloaded artifacts and core unit tests. `uv run --locked meteo-baseline run` performs the complete workflow from a clean checkout.
3. **the implementation — Implement model evaluation and baseline comparison:** raw/corrected RMSE, MAE, signed bias and skill on paired held-out rows; generated `evaluation.md` tables; resolved config/model/provenance metadata beside artifacts. Only the 24-hour lead is modeled, so no redundant lead-time stratification is generated.
