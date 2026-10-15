"""
Phase 7 (brief P6) — data validation, drift, monitoring, experiment tracking.

The brief's named acceptance test is `test_injected_drift_is_detected`: a
known shift is injected into the recent window and must be flagged, while an
identically-distributed window must not be.
"""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app
from app.quality import drift, monitoring, validation

N_LOTS = 40


@pytest.fixture(scope="module")
def sample(sim_tables):
    lots = sim_tables["lots"].head(N_LOTS).copy()
    telemetry = sim_tables["sensor_data"][sim_tables["sensor_data"]["lot_id"].isin(lots["lot_id"])].copy()
    return lots, telemetry


def _issues(report_df, lot_id, check):
    row = report_df.set_index("lot_id").loc[lot_id]
    return [i for i in row["issues"] if i["check"] == check]


# --------------------------------------------------------------------------- validation
def test_generated_data_has_no_hard_failures(sample):
    lots, telemetry = sample
    reports = validation.validate_tables(lots, telemetry)
    assert len(reports) == N_LOTS
    assert set(reports["status"]) <= {"PASS", "WARN"}, reports[reports["status"] == "FAIL"]["issues"].tolist()


def test_each_injected_problem_is_caught(sample):
    lots, telemetry = sample
    lots, tel = lots.copy(), telemetry.copy()
    ids = lots["lot_id"].tolist()

    tel = tel[~((tel["lot_id"] == ids[0]) & (tel["parameter"] == "vibration"))]                     # missing sensor
    mask = (tel["lot_id"] == ids[1]) & (tel["parameter"] == "pressure") & tel["ts_index"].between(10, 15)
    tel.loc[mask, "value"] = np.nan                                                                  # outage
    mask = (tel["lot_id"] == ids[2]) & (tel["parameter"] == "temperature")
    tel.loc[mask, "value"] = tel.loc[mask, "value"] + 273.15                                         # Kelvin
    mask = (tel["lot_id"] == ids[3]) & (tel["parameter"] == "rf_power") & (tel["ts_index"] == 5)
    tel.loc[mask, "value"] = 1e6                                                                     # impossible
    dup = tel[(tel["lot_id"] == ids[4]) & (tel["parameter"] == "gas_flow")].head(3)
    tel = pd.concat([tel, dup], ignore_index=True)                                                   # duplicates
    mask = (tel["lot_id"] == ids[5]) & (tel["parameter"] == "rf_power") & tel["ts_index"].between(20, 26)
    tel.loc[mask, "value"] = 857.0                                                                   # stuck
    mask = (tel["lot_id"] == ids[6]) & (tel["parameter"] == "pressure") & (tel["ts_index"] == 7)
    tel.loc[mask, "timestamp"] = "2001-01-01T00:00:00+00:00"                                         # bad timestamp
    lots.loc[lots["lot_id"] == ids[7], "yield"] = 150.0                                              # impossible yield
    lots = pd.concat([lots, lots[lots["lot_id"] == ids[8]]], ignore_index=True)                      # duplicate lot

    reports = validation.validate_tables(lots, tel)
    assert _issues(reports, ids[0], "missing_sensor")
    assert _issues(reports, ids[1], "sensor_outage")
    unit = _issues(reports, ids[2], "unit_mismatch")
    assert unit and "Kelvin" in unit[0]["detail"]
    assert not _issues(reports, ids[2], "impossible_values"), "a unit mismatch is reported once, as such"
    assert _issues(reports, ids[3], "impossible_values")
    assert _issues(reports, ids[4], "duplicate_samples")
    assert _issues(reports, ids[5], "stuck_values")
    assert _issues(reports, ids[6], "timestamp_order")
    assert _issues(reports, ids[7], "impossible_yield")
    assert _issues(reports, ids[8], "duplicate_lot_id")
    statuses = reports.set_index("lot_id")["status"]
    for lot_id in (ids[0], ids[2], ids[3], ids[4], ids[6], ids[7], ids[8]):
        assert statuses[lot_id] == "FAIL", lot_id
    assert statuses[ids[1]] in {"WARN", "FAIL"} and statuses[ids[5]] in {"WARN", "FAIL"}

    summary = validation.summarize(reports)
    assert summary["n_lots"] == N_LOTS and summary["status_counts"]["FAIL"] >= 7


# --------------------------------------------------------------------------- drift
def test_injected_drift_is_detected():
    rng = np.random.default_rng(0)
    reference = pd.DataFrame(
        {
            "rf_power_mean": rng.normal(857.5, 3.0, 2000),
            "pressure_mean": rng.normal(2.75, 0.05, 2000),
            "recipe_id": rng.choice(["REC_001", "REC_002", "REC_003"], 2000),
        }
    )
    same = pd.DataFrame(
        {
            "rf_power_mean": rng.normal(857.5, 3.0, 600),
            "pressure_mean": rng.normal(2.75, 0.05, 600),
            "recipe_id": rng.choice(["REC_001", "REC_002", "REC_003"], 600),
        }
    )
    groups = {"sensors": ["rf_power_mean", "pressure_mean"], "categorical": ["recipe_id"]}

    stable = drift.compare(reference, same, groups)
    assert stable["overall_status"] == "stable", stable["drifted_features"]

    shifted = same.copy()
    shifted["rf_power_mean"] += 6.0                                    # +2 sigma RF drift
    shifted["recipe_id"] = rng.choice(["REC_001", "REC_009"], 600, p=[0.3, 0.7])  # new recipe mix
    report = drift.compare(reference, shifted, groups)
    assert report["overall_status"] == "drift"
    assert set(report["drifted_features"]) == {"rf_power_mean", "recipe_id"}
    rf = next(f for f in report["features"] if f["feature"] == "rf_power_mean")
    assert rf["psi_band"] == "significant" and rf["ks_p_value"] < drift.KS_ALPHA


def test_psi_is_zero_for_identical_samples_and_grows_with_shift():
    x = np.random.default_rng(1).normal(0, 1, 5000)
    assert drift.psi_numeric(x, x) == pytest.approx(0.0, abs=1e-9)
    assert drift.psi_numeric(x, x + 0.5) < drift.psi_numeric(x, x + 1.5)


def test_drift_report_from_db_has_windows_and_groups(db):
    report = drift.drift_report_from_db(db, window_days=30)
    assert report["windows"]["reference"]["n"] > report["windows"]["current"]["n"] > 0
    assert {f["group"] for f in report["features"]} >= {"sensors", "categorical"}


# --------------------------------------------------------------------------- monitoring
def test_daily_snapshots_reconcile_with_the_raw_predictions(db):
    frame = monitoring.monitoring_frame_from_db(db)
    snapshots = monitoring.build_daily_snapshots(frame, threshold=0.5, model_version="test")
    assert sum(s["n_predicted"] for s in snapshots) == len(frame)

    labelled = frame[frame["actual_yield"].notna()]
    flagged = labelled["probability"] >= 0.5
    is_low = labelled["actual_yield"] < 85.0
    assert sum(s["labelled"]["false_alarms"] for s in snapshots) == int((flagged & ~is_low).sum())
    assert sum(s["labelled"]["missed_low_yield"] for s in snapshots) == int((~flagged & is_low).sum())
    for s in snapshots:
        if s["calibration"]["ece"] is not None:
            assert 0.0 <= s["calibration"]["ece"] <= 1.0

    summary = monitoring.summarize_snapshots(snapshots)
    assert summary["n_days"] == len(snapshots) and summary["n_predicted"] == len(frame)


def test_ece_matches_a_hand_computed_case():
    y = np.array([0, 0, 1, 1], dtype=float)
    p = np.array([0.05, 0.05, 0.95, 0.95])
    assert monitoring.expected_calibration_error(y, p) == pytest.approx(0.05)


# --------------------------------------------------------------------------- tracking
def test_mlflow_run_is_logged_to_the_isolated_store(settings):
    pytest.importorskip("mlflow")
    from app.ops.tracking import log_run

    run_id = log_run("unit-test", "test_model", "0.0.1", params={"a": 1, "nested": {"b": 2}}, metrics={"x": {"mae": 1.5}})
    assert run_id
    import mlflow

    run = mlflow.get_run(run_id)
    assert run.data.metrics["x.mae"] == 1.5
    assert run.data.tags["dataset_version"] and run.data.tags["data_provenance"] == "synthetic"


def test_tracking_disabled_is_a_noop(settings, monkeypatch):
    from app.ops.tracking import log_run

    monkeypatch.setattr(settings, "mlflow_enabled", False)
    assert log_run("off", "m", "1") is None


# --------------------------------------------------------------------------- API
@pytest.fixture()
def admin_client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": "wafguard-admin"}).json()
        client.headers["Authorization"] = f"Bearer {token['data']['access_token']}"
        yield client
    app.dependency_overrides.clear()


def test_quality_endpoints(admin_client, db, sample):
    lots, telemetry = sample
    validation.store_reports(db, validation.validate_tables(lots, telemetry))
    lot_id = lots["lot_id"].iloc[0]

    summary = admin_client.get("/api/quality/summary").json()["data"]
    assert summary["n_lots_reported"] >= N_LOTS
    report = admin_client.get(f"/api/quality/lots/{lot_id}")
    assert report.status_code == 200 and report.json()["data"]["status"] in {"PASS", "WARN"}
    assert admin_client.get("/api/quality/lots/LOT_NOPE").status_code == 404

    drift_body = admin_client.get("/api/quality/drift", params={"window_days": 30}).json()["data"]
    assert "features" in drift_body and drift_body["overall_status"] in {"stable", "drift"}

    built = admin_client.post("/api/quality/monitoring/snapshots")
    assert built.status_code == 200 and built.json()["data"]["stored"] > 0
    mon = admin_client.get("/api/quality/monitoring", params={"limit": 10}).json()["data"]
    assert len(mon["snapshots"]) == 10 and "SYNTHETIC" in mon["label"]


def test_monitoring_rebuild_is_admin_only(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            token = client.post("/api/auth/login", json={"username": "viewer", "password": "wafguard-viewer"}).json()
            client.headers["Authorization"] = f"Bearer {token['data']['access_token']}"
            assert client.post("/api/quality/monitoring/snapshots").status_code == 403
            assert client.get("/api/quality/monitoring").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_data_import_validates_on_ingest(admin_client, db):
    payload = {
        "lots": [
            {"lot_id": "LOT_IMPORT_OK", "product": "X", "process_start": "2026-09-01T00:00:00Z", "equipment": "ETCH_001", "process_step": "ETCH", "yield": 95.0},
            {"lot_id": "LOT_IMPORT_BAD", "product": "X", "process_start": "2026-09-01T00:00:00Z", "equipment": "ETCH_001", "process_step": "ETCH", "yield": 140.0},
        ]
    }
    try:
        response = admin_client.post("/api/data/import", json=payload)
        assert response.status_code == 200, response.text
        quality = response.json()["data"]["data_quality"]
        assert quality["status_counts"].get("FAIL") == 1
        assert db.data_quality_reports.find_one({"lot_id": "LOT_IMPORT_BAD"})["status"] == "FAIL"
    finally:
        db.lots.delete_many({"lot_id": {"$in": ["LOT_IMPORT_OK", "LOT_IMPORT_BAD"]}})
        db.data_quality_reports.delete_many({"lot_id": {"$in": ["LOT_IMPORT_OK", "LOT_IMPORT_BAD"]}})
