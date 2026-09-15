"""Pure unit tests for risk_service — no DB needed."""
from app.services.risk_service import (
    compute_equipment_risk_score,
    compute_historical_defect_risk,
    compute_parameter_deviation_score,
    compute_risk,
    raw_parameter_deviation,
    risk_level_from_score,
)


def test_risk_level_bands():
    assert risk_level_from_score(0) == "LOW"
    assert risk_level_from_score(30) == "LOW"
    assert risk_level_from_score(31) == "MEDIUM"
    assert risk_level_from_score(60) == "MEDIUM"
    assert risk_level_from_score(61) == "HIGH"
    assert risk_level_from_score(100) == "HIGH"


def test_raw_parameter_deviation_center_and_edge():
    # rf_power normal range is (840, 875) -> center 857.5, half-width 17.5
    assert raw_parameter_deviation("rf_power", 857.5) == 0.0
    assert abs(raw_parameter_deviation("rf_power", 875.0) - 1.0) < 1e-9
    assert raw_parameter_deviation("rf_power", None) == 0.0
    assert raw_parameter_deviation("not_a_real_param", 100) == 0.0


def test_parameter_deviation_score_uses_worst_parameter():
    normal = {"rf_power": 857.5, "temperature": 437.5, "pressure": 2.75}
    abnormal = {"rf_power": 920.0, "temperature": 437.5, "pressure": 2.75}
    assert compute_parameter_deviation_score(normal) == 0.0
    assert compute_parameter_deviation_score(abnormal) > compute_parameter_deviation_score(normal)


def test_equipment_risk_score_reflects_health_and_anomaly():
    healthy = {"health_score": 98.0, "anomaly_status": None}
    unhealthy = {"health_score": 60.0, "anomaly_status": "RF power instability"}
    assert compute_equipment_risk_score(None) == 0.0
    assert compute_equipment_risk_score(healthy) < compute_equipment_risk_score(unhealthy)


def test_historical_defect_risk_fraction():
    assert compute_historical_defect_risk([]) == 0.0
    assert compute_historical_defect_risk([90, 92, 95]) == 0.0
    assert compute_historical_defect_risk([90, 70, 95, 60], low_yield_threshold=85) == 0.5


def test_compute_risk_is_bounded_and_monotonic_in_model_probability():
    params = {"rf_power": 857.5, "temperature": 437.5, "pressure": 2.75}
    low = compute_risk(0.05, params, None, 0.0)
    high = compute_risk(0.95, params, None, 0.0)
    assert 0.0 <= low["risk_probability"] <= 1.0
    assert 0.0 <= high["risk_probability"] <= 1.0
    assert high["risk_probability"] > low["risk_probability"]
    assert low["risk_level"] in ("LOW", "MEDIUM", "HIGH")
    assert "weights" in high["breakdown"]
