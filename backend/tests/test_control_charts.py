"""
Phase 5 SPC tests (P5-4): running-z math is strictly backward-looking, the
R1/R2/R3 rules fire on hand-built series, and the service/API contracts hold
on the seeded database.
"""
import pytest

from app.services import control_chart_service


def _points(values):
    zs = control_chart_service._running_z(values)
    return [
        {"lot_id": f"L{i}", "value": v, "z": z}
        for i, (v, z) in enumerate(zip(values, zs))
    ]


def test_running_z_never_uses_future_or_self():
    values = [10.0] * 8 + [30.0]
    zs = control_chart_service._running_z(values)
    assert all(z is None for z in zs[:8]), "fewer than 8 prior points -> no z"
    assert zs[8] > 5, "spike after stable history must be strongly positive"


def test_running_z_flat_history_gives_zero_sigma():
    values = [5.0] * 12
    zs = control_chart_service._running_z(values)
    assert zs[8] == 0.0  # zero sigma -> treat as at-centerline, never a breach


def test_rule_r1_fires_beyond_3_sigma():
    values = [10.0, 10.5, 9.5, 10.0, 9.8, 10.2, 10.0, 10.5, 14.0]
    breaches = control_chart_service._detect_breaches(_points(values))
    assert any(b["rule"] == "R1" for b in breaches)


def test_rule_r2_fires_two_of_three_beyond_2_sigma():
    # stable at 10 (sigma 0 -> z 0 for prior-flat), then push points above 2σ
    values = [10, 11, 9, 10, 9, 11, 10, 10, 12.5, 12.6, 10.0, 12.7]
    breaches = control_chart_service._detect_breaches(_points(values))
    assert any(b["rule"] == "R2" for b in breaches)


def test_rule_r3_fires_seven_point_trend():
    values = list(range(10)) + [10.0 + i for i in range(7)]  # 7 rising points
    breaches = control_chart_service._detect_breaches(_points(values))
    assert any(b["rule"] == "R3" for b in breaches)


def test_no_breaches_on_clean_series():
    values = [10, 10.2, 9.8, 10.1, 9.9, 10.2, 10.0, 9.8, 10.1, 10.0, 9.9, 10.1]
    breaches = control_chart_service._detect_breaches(_points(values))
    assert breaches == []


# --------------------------------------------------------------------------- service + API
def test_compute_control_chart_on_seeded_db(db):
    lot = db.lots.find_one({})
    chart = control_chart_service.compute_control_chart(db, lot["equipment"], "rf_power")
    assert chart["parameter"] == "rf_power"
    assert chart["n_history"] > 0
    assert chart["granularity"].startswith("lot-level")
    assert chart["centerline"] is not None
    for point in chart["points"]:
        assert set(point) == {"lot_id", "ts", "value", "z"}


def test_unknown_parameter_and_equipment_rejected(db):
    from app.utils.exceptions import NotFoundError

    lot = db.lots.find_one({})
    with pytest.raises(NotFoundError):
        control_chart_service.compute_control_chart(db, lot["equipment"], "vibration")
    with pytest.raises(NotFoundError):
        control_chart_service.compute_control_chart(db, "NO_SUCH_TOOL", "rf_power")


@pytest.fixture()
def client(db):
    from app.api.deps import get_db
    from app.main import app
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        login = test_client.post("/api/auth/login", json={"username": "admin", "password": "wafguard-admin"})
        assert login.status_code == 200, login.text
        test_client.headers["Authorization"] = f"Bearer {login.json()['data']['access_token']}"
        yield test_client
    app.dependency_overrides.clear()


def test_control_chart_api_contract(client, db):
    lot = db.lots.find_one({})
    response = client.get(f"/api/equipment/{lot['equipment']}/control-chart?parameter=rf_power&limit=50")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["equipment_id"] == lot["equipment"]
    assert len(data["points"]) <= 50
    assert isinstance(data["breaches"], list)

    assert client.get(f"/api/equipment/{lot['equipment']}/control-chart?parameter=vibration").status_code == 404
    assert client.get("/api/equipment/NO_SUCH_TOOL/control-chart").status_code == 404
