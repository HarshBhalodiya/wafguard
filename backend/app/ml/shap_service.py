"""
SHAP explainability wrapper (spec section 13).

Terminology discipline matters here: SHAP explains how much each feature
pushed *this model's* output away from its baseline. It is model
attribution, not proof of a physical cause — callers must label results
as "root-cause candidate" / "strong model contributor", never "confirmed
root cause" (spec section 45).
"""
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import shap


@dataclass
class FeatureContribution:
    feature: str
    shap_value: float
    feature_value: float
    baseline_value: float
    direction: str  # "increases_risk" | "decreases_risk"
    relative_contribution: float  # 0-1, share of total |shap| across all features


def _extract_values_and_base(explanation, positive_class_index: int = 1):
    values = np.asarray(explanation.values)
    base_values = np.asarray(explanation.base_values)

    if values.ndim == 3:  # (n_samples, n_features, n_classes)
        values = values[..., positive_class_index]
    if base_values.ndim == 2:  # (n_samples, n_classes)
        base_values = base_values[:, positive_class_index]
    elif base_values.ndim == 0:
        base_values = np.full(values.shape[0], float(base_values))
    return values, base_values


def explain_instance(
    model: Any,
    X_row: pd.DataFrame,
    top_n: int = 8,
) -> list[FeatureContribution]:
    """X_row: a single-row DataFrame with the exact columns the model was trained on."""
    explainer = shap.TreeExplainer(model)
    explanation = explainer(X_row)
    values, base_values = _extract_values_and_base(explanation)

    row_values = values[0]
    baseline = float(base_values[0])
    total_abs = float(np.sum(np.abs(row_values))) or 1.0

    contributions = []
    for i, feature in enumerate(X_row.columns):
        shap_value = float(row_values[i])
        contributions.append(
            FeatureContribution(
                feature=feature,
                shap_value=shap_value,
                feature_value=float(X_row.iloc[0, i]),
                baseline_value=baseline,
                direction="increases_risk" if shap_value > 0 else "decreases_risk",
                relative_contribution=round(abs(shap_value) / total_abs, 4),
            )
        )

    contributions.sort(key=lambda c: abs(c.shap_value), reverse=True)
    return contributions[:top_n]


def global_feature_importance(model: Any, X_sample: pd.DataFrame, top_n: int = 15) -> list[dict]:
    """Mean |SHAP| across a sample of rows — used for the Model Performance 'feature analysis' panel."""
    explainer = shap.TreeExplainer(model)
    explanation = explainer(X_sample)
    values, _ = _extract_values_and_base(explanation)
    mean_abs = np.abs(values).mean(axis=0)
    order = np.argsort(mean_abs)[::-1][:top_n]
    return [{"feature": X_sample.columns[i], "mean_abs_shap": float(mean_abs[i])} for i in order]
