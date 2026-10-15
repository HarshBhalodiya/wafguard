"""
Phase 2 tests: temporal CV, extended features, calibration, temporal
dataset builder, and vision embeddings.

These are fast unit/property tests — they never train full models and
never touch disk artifacts or MongoDB.
"""
import numpy as np
import pandas as pd
import pytest
import torch

from app.ml.calibration import (
    expected_calibration_error,
    find_best_threshold,
    fit_standalone_calibrator,
    reliability_curve_data,
)
from app.ml.cv import (
    TemporalLeakageError,
    assert_temporal_order,
    check_split_leakage_free,
    expanding_window_folds,
)
from app.ml.preprocessing import NUMERIC_FEATURE_COLUMNS, chronological_split
from app.features.tabular import EXTENDED_NUMERIC_FEATURE_COLUMNS


# ---------------------------------------------------------------------------
# Time-series CV (app.ml.cv)
# ---------------------------------------------------------------------------

def test_expanding_window_folds_are_order_safe():
    folds = expanding_window_folds(n_samples=100, n_folds=4, min_train_frac=0.5)
    assert len(folds) == 4
    for fold in folds:
        assert fold.train_idx[-1] < fold.val_idx[0], "training window must end before validation starts"
    # Expanding: each fold's training window is a superset of the previous.
    for prev, cur in zip(folds, folds[1:]):
        assert set(prev.train_idx).issubset(set(cur.train_idx))
        assert cur.val_idx[0] > prev.val_idx[-1]


def test_expanding_window_folds_reject_bad_args():
    with pytest.raises(ValueError):
        expanding_window_folds(n_samples=100, n_folds=1)
    with pytest.raises(ValueError):
        expanding_window_folds(n_samples=4, n_folds=10)


def test_assert_temporal_order_detects_leakage():
    good = pd.DataFrame({"process_start": pd.to_datetime(["2026-01-01", "2026-01-02"])})
    later = pd.DataFrame({"process_start": pd.to_datetime(["2026-01-03", "2026-01-04"])})
    assert_temporal_order(good, later)  # no exception

    leaky_val = pd.DataFrame({"process_start": pd.to_datetime(["2026-01-01"])})
    with pytest.raises(TemporalLeakageError):
        assert_temporal_order(good, leaky_val)


def test_check_split_leakage_free_on_chronological_features():
    n = 60
    df = pd.DataFrame(
        {
            "lot_id": [f"L{i}" for i in range(n)],
            "process_start": pd.date_range("2026-01-01", periods=n, freq="6h"),
            "yield": np.linspace(95, 80, n),
        }
    )
    report = check_split_leakage_free(df)
    assert report["leakage_free"] is True


def test_chronological_split_is_monotonic_in_time():
    n = 90
    df = pd.DataFrame(
        {
            "lot_id": [f"L{i}" for i in range(n)],
            "process_start": pd.date_range("2026-01-01", periods=n, freq="1h"),
        }
    )
    train, val, test = chronological_split(df)
    assert pd.to_datetime(train.process_start).max() < pd.to_datetime(val.process_start).min()
    assert pd.to_datetime(val.process_start).max() < pd.to_datetime(test.process_start).min()


# ---------------------------------------------------------------------------
# Extended features (app.features.tabular)
# ---------------------------------------------------------------------------

def test_extended_feature_columns_are_registered():
    assert "temperature_slope" in NUMERIC_FEATURE_COLUMNS
    assert "pressure_excursions" in NUMERIC_FEATURE_COLUMNS
    assert "rf_power_time_outside_pct" in NUMERIC_FEATURE_COLUMNS
    assert "equipment_age_days" in NUMERIC_FEATURE_COLUMNS
    assert "chamber_previous_yield" in NUMERIC_FEATURE_COLUMNS
    assert "equipment_prev10_low_yield_rate" in NUMERIC_FEATURE_COLUMNS
    assert len(EXTENDED_NUMERIC_FEATURE_COLUMNS) == 15  # 4 params x 3 + 3 context


def test_sensor_shape_features_detect_excursions():
    from app.features.tabular import sensor_shape_features

    lo, hi = 2.4, 3.1  # pressure range
    rows = []
    for lot in ("A", "B"):
        for i in range(20):
            if lot == "A":
                value = 2.75  # always inside
            else:
                value = 4.0 if i % 2 == 0 else 2.75  # half outside
            rows.append({"lot_id": lot, "reading_index": i, "parameter": "pressure", "value": value})
    sensor_df = pd.DataFrame(rows)

    feats = sensor_shape_features(sensor_df)
    assert feats.loc["A", "pressure_excursions"] == 0.0
    assert feats.loc["A", "pressure_time_outside_pct"] == 0.0
    assert feats.loc["B", "pressure_excursions"] == pytest.approx(10.0)
    assert feats.loc["B", "pressure_time_outside_pct"] == pytest.approx(0.5)
    # Flat series: slope ~0; alternating series: negative slope.
    assert abs(feats.loc["A", "pressure_slope"]) < 1e-6


def test_equipment_context_features_are_backward_looking():
    from app.features.tabular import equipment_context_features

    lots = pd.DataFrame(
        {
            "lot_id": ["L1", "L2", "L3"],
            "equipment": ["E1", "E1", "E1"],
            "chamber_id": ["C1", "C1", "C1"],
            "process_start": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "yield": [60.0, 95.0, 90.0],  # L1 low, L2/L3 fine
        }
    )
    feats = equipment_context_features(lots)
    # First lot on the tool: age 0, no previous low-yield history.
    assert feats.loc["L1", "equipment_age_days"] == pytest.approx(0.0)
    assert feats.loc["L1", "equipment_prev10_low_yield_rate"] == pytest.approx(0.0)
    # After L1 (60% = low), the rate seen by L2 is 1.0; after L2, 0.5.
    assert feats.loc["L2", "equipment_prev10_low_yield_rate"] == pytest.approx(1.0)
    assert feats.loc["L3", "equipment_prev10_low_yield_rate"] == pytest.approx(0.5)
    # chamber_previous_yield for L2 = L1's yield.
    assert feats.loc["L2", "chamber_previous_yield"] == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# Calibration (app.ml.calibration)
# ---------------------------------------------------------------------------

def _make_toy_classifier():
    from sklearn.datasets import make_classification
    from sklearn.linear_model import LogisticRegression

    X, y = make_classification(
        n_samples=600, n_features=5, n_informative=3, weights=[0.85, 0.15],
        random_state=42, flip_y=0.15,
    )
    from sklearn.model_selection import train_test_split

    X_tr, X_va, y_tr, y_va = train_test_split(X, y, test_size=0.5, stratify=y, random_state=42)
    clf = LogisticRegression(max_iter=1000).fit(X_tr, y_tr)
    return clf, X_tr, y_tr, X_va, y_va


def test_isotonic_calibration_reduces_ece_on_toy_problem():
    from app.ml.calibration import calibrate_classifier

    clf, X_tr, y_tr, X_va, y_va = _make_toy_classifier()
    proba_before = clf.predict_proba(X_va)[:, 1]
    ece_before = expected_calibration_error(y_va, proba_before)

    calibrated = calibrate_classifier(clf, X_va, y_va, method="isotonic")
    proba_after = calibrated.predict_proba(X_va)[:, 1]
    ece_after = expected_calibration_error(y_va, proba_after)

    assert ece_after < ece_before
    assert proba_after.min() >= 0.0 and proba_after.max() <= 1.0


def test_reliability_curve_bins_sum_to_sample_count():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 500)
    p = rng.uniform(0, 1, 500)
    rows = reliability_curve_data(y, p, n_bins=10)
    assert sum(r["count"] for r in rows) == 500
    for r in rows:
        assert 0 <= r["mean_predicted"] <= 1
        assert 0 <= r["observed_frequency"] <= 1


def test_expected_calibration_error_is_zero_for_perfect_calibration():
    # 20 samples at p=0.5 with exactly half positives, 20 at p=0.8 with
    # exactly 80% positives — predicted frequency == observed frequency.
    p = np.array([0.5] * 20 + [0.8] * 20)
    y = np.array([1, 0] * 10 + [1, 1, 1, 1, 0] * 4)
    ece = expected_calibration_error(y, p, n_bins=10)
    assert ece < 1e-9


def test_find_best_threshold_maximizes_f1_better_than_fixed_half():
    rng = np.random.default_rng(1)
    proba = rng.uniform(0, 1, 800)
    y = (proba > 0.3).astype(int)  # signals concentrate above 0.3
    thr = find_best_threshold(y, proba)

    from sklearn.metrics import f1_score

    f1_best = f1_score(y, (proba >= thr).astype(int))
    f1_half = f1_score(y, (proba >= 0.5).astype(int))
    assert f1_best >= f1_half - 1e-9
    assert 0.0 <= thr <= 1.0


def test_fit_standalone_calibrator_maps_to_valid_range():
    rng = np.random.default_rng(2)
    p = rng.uniform(0, 1, 400)
    y = (p > 0.6).astype(float)
    for method in ("isotonic", "sigmoid"):
        cal = fit_standalone_calibrator(y, p, method=method)
        out = cal(np.array([0.0, 0.5, 1.0]))
        assert np.all(out >= 0.0) and np.all(out <= 1.0)


# ---------------------------------------------------------------------------
# Temporal dataset builder (app.features.temporal_dataset)
# ---------------------------------------------------------------------------

def _toy_sensor_df():
    rows = []
    # Lot A: full 6-step series in 2 channels
    for i in range(6):
        rows.append({"lot_id": "A", "parameter": "temperature", "ts_index": i, "value": 430.0 + i})
        rows.append({"lot_id": "A", "parameter": "pressure", "ts_index": i, "value": 2.75})
        rows.append({"lot_id": "A", "parameter": "temperature", "ts_index": i, "value": 430.0 + i})  # dup kept simple
    # Lot B: sensor_dropout — temperature only, 2 steps
    for i in range(2):
        rows.append({"lot_id": "B", "parameter": "temperature", "ts_index": i, "value": 440.0})
    return pd.DataFrame(rows)


def test_build_temporal_sequences_mask_marks_real_readings():
    from app.features.temporal_dataset import build_temporal_sequences

    X, mask = build_temporal_sequences(_toy_sensor_df(), ["A", "B"], max_len=6, params=["temperature", "pressure"])
    assert X.shape == (2, 6, 2)
    # Lot A, temperature: 6 real readings
    assert mask[0, :, 0].sum() == 6.0
    # Lot A, pressure: 6 real readings
    assert mask[0, :, 1].sum() == 6.0
    # Lot B: only 2 temperature readings, pressure entirely missing
    assert mask[1, :, 0].sum() == 2.0
    assert mask[1, :, 1].sum() == 0.0
    # Missing entries are exactly zero in X
    assert np.all(X[1, 2:, :] == 0.0)


def test_scaler_uses_train_stats_only_and_masks_stay_zero():
    from app.features.temporal_dataset import apply_scaler, build_temporal_sequences, fit_scaler

    X, mask = build_temporal_sequences(_toy_sensor_df(), ["A", "B"], max_len=6, params=["temperature"])
    scaler = fit_scaler(X[:1], mask[:1])  # lot A only
    Xn = apply_scaler(X, mask, scaler)
    # Where mask==0, output stays exactly 0 (no scaled noise).
    assert np.all(Xn[1, 2:, 0] == 0.0)
    # Lot A values are standardized with lot-A stats.
    assert abs(Xn[0, :, 0][mask[0, :, 0] == 1].mean()) < 1e-5


def test_temporal_models_forward_with_mask():
    from app.ml.temporal.gru_model import TemporalGRU
    from app.ml.temporal.transformer import TemporalTransformer

    x = torch.randn(4, 10, 2)
    mask = torch.ones(4, 10, 2)
    mask[:, 8:, :] = 0.0  # last two steps missing

    for model in (TemporalGRU(n_channels=2), TemporalTransformer(n_channels=2)):
        risk, yld, emb = model(x, mask)
        assert risk.shape == (4,) and yld.shape == (4,)
        assert emb.shape[0] == 4 and torch.isfinite(emb).all()


# ---------------------------------------------------------------------------
# Vision embeddings (app.ml.vision)
# ---------------------------------------------------------------------------

def test_extract_embeddings_shapes_and_determinism():
    from app.ml.vision.architectures import SmallResNet
    from app.ml.vision.embeddings import extract_embeddings

    model = SmallResNet(num_classes=9)
    model.eval()
    X = np.random.RandomState(0).randn(8, 3, 64, 64).astype(np.float32)
    e1 = extract_embeddings(model, X)
    e2 = extract_embeddings(model, X)
    assert e1.shape == (8, 64)
    assert np.allclose(e1, e2)


def test_vision_backbones_share_forward_contract():
    from app.ml.vision.architectures import SmallResNet, SmallViT
    from app.ml.wafer_model import WaferCNN

    x = torch.randn(2, 3, 64, 64)
    for model in (WaferCNN(), SmallResNet(num_classes=9), SmallViT(num_classes=9)):
        logits, feats = model(x)
        assert logits.shape == (2, 9)
        assert feats.dim() == 4  # spatial features for CAM-style tooling
