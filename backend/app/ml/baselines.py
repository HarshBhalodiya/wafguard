"""
Simple baseline models (Phase 2A) so the production model's numbers are
always reported against something honest (see reports/tabular_comparison.md):

  - mean predictor: predict the training mean (regression) or the training
    positive rate (classification) for everything
  - ridge regression / logistic regression (linear baselines)
  - RandomForest (the Phase 1 baseline, kept)

None of these are "dumb on purpose": if a linear or forest model matches
the tuned XGBoost on the chronological test split, the report says so.
"""
import numpy as np
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


def fit_regressor_baselines(X_train, y_train: np.ndarray) -> dict:
    ridge = make_pipeline(
        StandardScaler(),
        Ridge(alpha=1.0, random_state=0),
    )
    ridge.fit(X_train, y_train)
    return {
        "mean_predictor": DummyRegressor(strategy="mean").fit(X_train, y_train),
        "ridge": ridge,
    }


def fit_classifier_baselines(X_train, y_train: np.ndarray) -> dict:
    positive_rate = float(np.mean(y_train))
    logreg = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=2000, class_weight="balanced", random_state=0),
    )
    logreg.fit(X_train, y_train)
    return {
        "base_rate_predictor": DummyClassifier(strategy="constant", constant=1 if positive_rate >= 0.5 else 0).fit(
            X_train, y_train
        ),
        "logreg": logreg,
    }
