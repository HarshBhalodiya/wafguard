"""
Phase 6 (brief P5) — counterfactual what-if with uncertainty.

Pins: impossible changes are blocked (not predicted); every output is a range
with a measured-coverage interval; the brief's label is present; a no-op change
is a zero difference; nothing is persisted; the old simulator still works.
"""
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app
from app.utils.exceptions import ValidationFailedError
from app.whatif import counterfactual
from app.whatif.ensemble import load_ensemble


@pytest.fixture(scope="module")
def ensemble(settings, trained_bundles):
    import train_whatif_ensemble

    bundle, _ = train_whatif_ensemble.train(settings, n_members=4, features_df=trained_bundles["features_df"])
    return bundle


@pytest.fixture()
def client(db, ensemble):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        token = test_client.post("/api/auth/login", json={"username": "engineer", "password": "wafguard-engineer"}).json()
        test_client.headers["Authorization"] = f"Bearer {token['data']['access_token']}"
        yield test_client
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- limits
@pytest.mark.parametrize(
    "changes, fragment",
    [
        ({"rf_power": 2000.0}, "physical limit"),
        ({"rf_power": 880.0}, "recipe window"),
        ({"pressure": -1.0}, "physical limit"),
        ({"maintenance_days_since_last": -3.0}, "physical limit"),
        ({"etch_time": 30.0}, "Constant in the v2 synthetic data"),
        ({"bogus": 1.0}, "unknown parameter"),
        ({}, "No change requested"),
    ],
)
def test_impossible_or_unknown_changes_are_blocked(changes, fragment):
    with pytest.raises(ValidationFailedError) as exc:
        counterfactual.validate_changes(changes)
    assert fragment in str(exc.value)


def test_limits_file_is_consistent():
    for name, spec in counterfactual.load_limits()["parameters"].items():
        r_lo, r_hi = spec["recipe_window"]
        p_lo, p_hi = spec["physical"]
        assert p_lo <= r_lo < r_hi <= p_hi, f"{name}: recipe window must sit inside the physical limit"


def test_sensor_change_shifts_level_and_holds_shape():
    before = {"rf_power_mean": 850.0, "rf_power_max": 870.0, "rf_power_std": 3.0, "rf_power_slope": 0.2}
    after, held = counterfactual.apply_changes(before, {"rf_power": 858.0})
    assert after["rf_power_mean"] == 858.0
    assert after["rf_power_max"] == pytest.approx(878.0)
    assert after["rf_power_std"] == 3.0 and after["rf_power_slope"] == 0.2
    assert "rf_power_std" in held and "rf_power_slope" in held
    assert before["rf_power_mean"] == 850.0, "apply_changes must not mutate its input"


# --------------------------------------------------------------------------- the engine
def test_counterfactual_returns_ranges_and_the_label(db, incident_lot_id, ensemble):
    result = counterfactual.run_counterfactual(db, incident_lot_id, {"rf_power": 857.5, "maintenance_days_since_last": 0.0})
    assert result["label"] == "Model-based counterfactual. Not proof that this change will produce this result."
    for key in ("yield_before", "yield_after", "yield_difference"):
        band = result[key]
        assert band["p10"] <= band["p50"] <= band["p90"], key
    for key in ("yield_before", "yield_after"):
        lo, hi = result[key]["interval_80"]
        assert lo <= hi
    method = result["uncertainty_method"]
    assert 0.0 <= method["measured_test_coverage_of_interval_80"] <= 1.0
    assert result["persisted"] is False
    assert {c["parameter"] for c in result["changes"]} == {"rf_power", "maintenance_days_since_last"}


def test_noop_change_has_zero_difference(db, incident_lot_id, ensemble):
    snapshot = db.predictions.find_one({"lot_id": incident_lot_id})["features_snapshot"]
    observed = snapshot["rf_power_mean"]
    lo, hi = counterfactual.load_limits()["parameters"]["rf_power"]["recipe_window"]
    if not lo <= observed <= hi:
        pytest.skip("incident lot's observed RF level is outside the recipe window, so a no-op is not allowed")
    result = counterfactual.run_counterfactual(db, incident_lot_id, {"rf_power": observed})
    assert result["yield_difference"] == {**result["yield_difference"], "p10": 0.0, "p50": 0.0, "p90": 0.0}


def test_counterfactual_persists_nothing(db, incident_lot_id, ensemble):
    before_counts = {name: db[name].count_documents({}) for name in ("predictions", "lots", "root_causes")}
    lot_before = db.lots.find_one({"lot_id": incident_lot_id})
    counterfactual.run_counterfactual(db, incident_lot_id, {"pressure": 2.75})
    assert {name: db[name].count_documents({}) for name in before_counts} == before_counts
    assert db.lots.find_one({"lot_id": incident_lot_id}) == lot_before


def test_ensemble_metrics_are_measured(settings, ensemble):
    loaded = load_ensemble(settings.resolve_path(settings.model_dir))
    assert loaded is not None and len(loaded.members) == 4
    ci = loaded.metrics["conformal_interval"]
    assert ci["nominal_coverage"] == 0.8
    assert 0.0 <= ci["test_coverage"] <= 1.0 and ci["mean_width"] > 0


# --------------------------------------------------------------------------- the API
def test_whatif_endpoints(client, incident_lot_id):
    limits = client.get("/api/whatif/limits")
    assert limits.status_code == 200 and "Not proof" in limits.json()["data"]["label"]

    ok = client.post("/api/whatif", json={"lot_id": incident_lot_id, "changes": {"gas_flow": 51.5}})
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["yield_after"]["p50"] is not None

    blocked = client.post("/api/whatif", json={"lot_id": incident_lot_id, "changes": {"gas_flow": 500.0}})
    assert blocked.status_code == 422
    assert "physical limit" in blocked.text

    assert client.post("/api/whatif", json={"lot_id": "LOT_NOPE", "changes": {"gas_flow": 50.0}}).status_code == 404


def test_viewer_cannot_run_whatif(db, incident_lot_id, ensemble):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as c:
            token = c.post("/api/auth/login", json={"username": "viewer", "password": "wafguard-viewer"}).json()
            c.headers["Authorization"] = f"Bearer {token['data']['access_token']}"
            assert c.post("/api/whatif", json={"lot_id": incident_lot_id, "changes": {"gas_flow": 51.5}}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_legacy_simulator_is_unchanged_and_the_new_engine_blocks(client, incident_lot_id):
    """The brief: keep the current simulator. /api/what-if behaves as before (its own
    tests use out-of-limit values); impossible values are blocked by /api/whatif."""
    assert client.post("/api/what-if", json={"lot_id": incident_lot_id, "overrides": {"rf_power": 858}}).status_code == 200
    blocked = client.post("/api/whatif", json={"lot_id": incident_lot_id, "changes": {"rf_power": 5000}})
    assert blocked.status_code == 422 and "physical limit" in blocked.text
