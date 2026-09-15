# Risk & Root-Cause Scoring Methodology

Spec requirement: risk and root-cause scoring must be "explicit and configurable", not a set of arbitrary percentages buried in code. This document is the explicit part; `backend/app/config.py` (`RISK_WEIGHTS`, `RISK_THRESHOLDS`, `ROOTCAUSE_WEIGHTS`, `ROOTCAUSE_CONFIDENCE_THRESHOLDS`) is the configurable part. Implementation lives in `app/services/risk_service.py` and `app/services/rootcause_service.py`.

## Risk score

```
risk_score (0-100) = 100 × ( w1·model_probability
                            + w2·parameter_deviation
                            + w3·equipment_risk
                            + w4·historical_defect_risk )
```

Default weights (`RISK_WEIGHTS`): model_probability 0.45, parameter_deviation 0.25, equipment_risk 0.20, historical_defect_risk 0.10. These sum to 1.0 by construction so the score stays bounded in [0, 100].

| Term | Range | How it's computed |
|---|---|---|
| `model_probability` | 0-1 | `lot_yield_classifier`'s (XGBoost) P(low-yield) for this lot's feature snapshot |
| `parameter_deviation` | 0-1 | The *worst* tracked process parameter's distance from its normal-range center, soft-thresholded: `clip((max_deviation - 0.3) / 1.7, 0, 1)`. Uses the max, not the average, because in practice one drifting parameter drives risk, not a mild wobble across all seven. |
| `equipment_risk` | 0-1 | `(100 - health_score) / 100`, +0.15 (capped at 1.0) if the equipment has a non-null `anomaly_status` |
| `historical_defect_risk` | 0-1 | Fraction of this equipment's last 10 lots (by `process_start`, excluding the current one) whose actual yield was below `LOW_YIELD_THRESHOLD` (85%) |

`risk_level` is then thresholded (`RISK_THRESHOLDS`): LOW ≤ 30, MEDIUM 31-60, HIGH ≥ 61.

**Why a soft threshold on parameter deviation, not a hard cutoff?** A real process window has a soft margin, not a cliff — a reading at 95% of the way to the edge of "normal" is riskier than one at the center, even if neither is technically "abnormal" yet. `raw_parameter_deviation(name, value)` returns a continuous value (0 at the center, 1.0 exactly at the historical range edge, >1 beyond it), and the risk score only starts counting it once it's 30% of the way past center, saturating at the edge — this keeps normal operating noise from constantly nudging the score.

## Root-cause candidate score

For each of ten candidates (seven process parameters + three equipment-history factors — see `CANDIDATE_DEFINITIONS` in `rootcause_service.py`), six signals are combined:

```
candidate_score (0-1) = w1·shap_attribution
                       + w2·parameter_deviation
                       + w3·historical_occurrence
                       + w4·equipment_association
                       + w5·wafer_pattern
                       + w6·temporal_evidence
```

Default weights (`ROOTCAUSE_WEIGHTS`): shap_attribution 0.30, parameter_deviation 0.20, historical_occurrence 0.15, equipment_association 0.15, wafer_pattern 0.10, temporal_evidence 0.10.

| Signal | How it's computed |
|---|---|
| `shap_attribution` | Sum of `|SHAP value|` across this candidate's underlying model features (e.g. `rf_power_mean`, `rf_power_max`, `rf_power_std`, `rf_power_drift` for the "RF Power" candidate), divided by the total `|SHAP value|` across *all* features |
| `parameter_deviation` | Same `raw_parameter_deviation` as the risk score, clipped to [0, 1] (no soft-knee offset here — this is meant to be read directly as "how deviated is this reading", not squashed into a risk contribution) |
| `historical_occurrence` | Of this equipment's recent MEDIUM/HIGH-risk lots, the fraction where this same candidate's parameter was *also* deviating beyond 0.8 |
| `equipment_association` | 0.9 if the equipment's `anomaly_status` text mentions this parameter; else 0.4 if the equipment's health score is below 80; else 0.1. For the three equipment-history candidates, this is just the equipment risk score directly — those candidates *are* about equipment condition. |
| `wafer_pattern` | A small fixed lookup (`WAFER_PATTERN_CANDIDATE_BOOST`) from the lot's dominant classified wafer pattern to a boost per candidate — e.g. Edge-Ring boosts RF Power (0.70) and Pressure (0.30). This is a heuristic association, not a physical model, and is always surfaced to the UI as `kind: INFERENCE`. |
| `temporal_evidence` | For parameters with a `_drift` feature (change across this run's sensor readings), `clip(|drift| / (half_width × 0.6), 0, 1)` |

Confidence bands (`ROOTCAUSE_CONFIDENCE_THRESHOLDS`): HIGH ≥ 0.70, MEDIUM ≥ 0.40, otherwise LOW.

**Terminology discipline.** Every evidence item returned by the root-cause engine is tagged FACT, MODEL_OUTPUT, or INFERENCE (recommendations get RECOMMENDATION). The engine's output is always called a "root-cause candidate", never a "confirmed root cause" — SHAP explains model attribution, not physical causality, and the wafer-pattern association is explicitly a correlation-shaped heuristic, not a proven mechanism.

## Changing the weights

All four weight/threshold dictionaries live in `backend/app/config.py` as plain module-level dicts — change a value and restart the backend; no retraining needed (these are scoring-layer weights, not model parameters). If you rebalance `RISK_WEIGHTS` or `ROOTCAUSE_WEIGHTS`, keep each dict's values summing to 1.0 so the resulting score stays bounded the way this document describes.
