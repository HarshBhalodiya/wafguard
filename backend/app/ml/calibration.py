"""
Probability calibration for the low-yield risk classifier (Phase 2A).

XGBoost probabilities are not calibrated out of the box; since the risk
score multiplies model_probability by 0.45 (app.config.RISK_WEIGHTS),
miscalibrated probabilities silently skew every risk level the dashboard
shows. Phase 2A therefore fits a calibrator on the chronological
*validation* split (cv="prefit" — the test split stays untouched) and
reports before/after calibration quality:

  - reliability curve data (per-bin observed frequency vs. predicted)
  - Expected Calibration Error (ECE)

A plot of the reliability curve is written by scripts/compare_tabular_models.py
to reports/calibration_reliability.png.
"""
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


def calibrate_classifier(model, X_val, y_val, method: str = "isotonic"):
    """
    Wraps a prefit classifier with a calibrator fit on the validation split.
    method: "isotonic" (non-parametric, needs data) or "sigmoid" (Platt).
    The returned object supports predict / predict_proba like the original.

    sklearn >= 1.6 removed cv="prefit"; FrozenEstimator is the supported
    way to keep the base estimator frozen while the calibrator fits on the
    held-out validation split.
    """
    if method not in ("isotonic", "sigmoid"):
        raise ValueError(f"unknown calibration method: {method}")
    calibrated = CalibratedClassifierCV(estimator=FrozenEstimator(model), method=method)
    calibrated.fit(X_val, y_val)
    return calibrated


def reliability_curve_data(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> list[dict]:
    """Per-bin mean predicted probability vs. observed positive frequency."""
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    bin_ids = np.clip(np.digitize(y_prob, bins) - 1, 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        mask = bin_ids == b
        count = int(mask.sum())
        if count == 0:
            continue
        rows.append(
            {
                "bin": b,
                "bin_low": float(bins[b]),
                "bin_high": float(bins[b + 1]),
                "mean_predicted": float(y_prob[mask].mean()),
                "observed_frequency": float(y_true[mask].mean()),
                "count": count,
            }
        )
    return rows


def expected_calibration_error(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> float:
    """ECE = sum over bins of (bin weight) * |mean predicted - observed frequency|."""
    rows = reliability_curve_data(y_true, y_prob, n_bins=n_bins)
    total = len(y_prob)
    if total == 0:
        return 0.0
    return float(sum(r["count"] * abs(r["mean_predicted"] - r["observed_frequency"]) for r in rows) / total)


def find_best_threshold(y_val: np.ndarray, prob_val: np.ndarray) -> float:
    """
    Picks the decision threshold maximizing F1 on a held-out split.

    Calibration changes the probability scale, so a threshold of 0.5 on
    calibrated scores is no longer the same operating point as 0.5 on raw
    XGBoost scores. The threshold is chosen on the validation split only,
    then applied unchanged to test.
    """
    from sklearn.metrics import precision_recall_curve

    precision, recall, thresholds = precision_recall_curve(
        np.asarray(y_val, dtype=int), np.asarray(prob_val, dtype=float)
    )
    f1 = np.divide(
        2 * precision[:-1] * recall[:-1],
        np.maximum(precision[:-1] + recall[:-1], 1e-12),
    )
    best = int(np.argmax(f1))
    return float(thresholds[best])


def fit_standalone_calibrator(y_val: np.ndarray, prob_val: np.ndarray, method: str = "isotonic"):
    """
    Fits a raw probability -> calibrated probability mapping on a held-out
    split, for use where a sklearn-wrapper (e.g. the temporal models) emits
    probabilities without a CalibratedClassifierCV-compatible API.
    Returns a callable.
    """
    prob_val = np.asarray(prob_val, dtype=float).reshape(-1)
    y_val = np.asarray(y_val, dtype=float)
    if method == "isotonic":
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(prob_val, y_val)
        return lambda p: np.clip(iso.predict(np.asarray(p, dtype=float).reshape(-1)), 0.0, 1.0)
    if method == "sigmoid":
        lr = LogisticRegression()
        lr.fit(prob_val.reshape(-1, 1), y_val)
        return lambda p: lr.predict_proba(np.asarray(p, dtype=float).reshape(-1, 1))[:, 1]
    raise ValueError(f"unknown calibration method: {method}")


def plot_reliability(curves: dict, out_path, title: str = "Reliability diagram (validation split)") -> None:
    """
    curves: {label: reliability_curve_data(...)} — draws one line per label
    plus the perfect-calibration diagonal. Used for the phase report.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], "k--", linewidth=1, label="perfectly calibrated")
    for label, rows in curves.items():
        xs = [r["mean_predicted"] for r in rows]
        ys = [r["observed_frequency"] for r in rows]
        ax.plot(xs, ys, marker="o", markersize=3, linewidth=1, label=label)
    ax.set_xlabel("mean predicted probability")
    ax.set_ylabel("observed low-yield frequency")
    ax.set_title(title)
    ax.legend(loc="best", fontsize=8)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
