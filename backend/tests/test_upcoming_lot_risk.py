"""
PHASE 4 tests — pre-run (upcoming lot) risk.

The load-bearing test is `test_future_mutation_does_not_change_the_score`:
every lot at or after the planned start is mutated and the estimate must be
byte-identical. Its positive control (`test_prior_mutation_does_change_the_score`)
proves the prior window is genuinely read, so the first test cannot pass by
accident.
"""
import copy

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.database.repositories.lot_repository import LotRepository
from app.main import app
from app.risk import upcoming_lot_risk
from app.utils.exceptions import NotFoundError, ValidationFailedError


def _planned_run_lot(db) -> dict:
    """A lot in the middle of the dataset whose tool already has a prior run."""
    repo = LotRepository(db)
    lots = sorted(repo.find_many({}), key=lambda lot: upcoming_lot_risk.parse_time(lot["process_start"]))
    for lot in lots[len(lots) // 2 :]:
        same_tool = repo.find_many({"equipment": lot["equipment"]})
        earlier = [
            other
            for other in same_tool
            if upcoming_lot_risk.parse_time(other["process_start"]) < upcoming_lot_risk.parse_time(lot["process_start"])
        ]
        if earlier:
            return lot
    pytest.fail("conftest dataset has no lot with a prior run on its own tool")


@pytest.fixture()
def planned_run(db) -> dict:
    lot = _planned_run_lot(db)
    return {"equipment_id": lot["equipment"], "planned_start": lot["process_start"]}


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        login = test_client.post("/api/auth/login", json={"username": "admin", "password": "wafguard-admin"})
        assert login.status_code == 200, login.text
        test_client.headers["Authorization"] = f"Bearer {login.json()['data']['access_token']}"
        yield test_client
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- pre-run only
def test_estimate_reports_its_sources_and_they_are_all_prior(db, planned_run):
    result = upcoming_lot_risk.score_planned_run(db, planned_run["equipment_id"], planned_run["planned_start"])

    assert result["label"]
    assert result["proxy_note"]
    assert result["sources"]["cutoff"] == "every input row has process_start < planned_start"
    assert result["sources"]["n_prior_lots_on_tool"] > 0

    planned_start = upcoming_lot_risk.parse_time(planned_run["planned_start"])
    source_lot = LotRepository(db).get_by_lot_id(result["sources"]["state_source_lot_id"])
    assert source_lot is not None
    assert upcoming_lot_risk.parse_time(source_lot["process_start"]) < planned_start

    for match in result["similar_lots"]:
        matched = LotRepository(db).get_by_lot_id(match["lot_id"])
        assert upcoming_lot_risk.parse_time(matched["process_start"]) < planned_start, (
            "a similar lot from the future was cited"
        )


def test_future_mutation_does_not_change_the_score(db, planned_run):
    """The brief's requirement, proven directly: nothing at/after the planned
    start can influence the estimate."""
    baseline = upcoming_lot_risk.score_planned_run(db, planned_run["equipment_id"], planned_run["planned_start"])

    planned_start = upcoming_lot_risk.parse_time(planned_run["planned_start"])
    all_lots = LotRepository(db).find_many({})
    future_lots = [
        lot for lot in all_lots if upcoming_lot_risk.parse_time(lot["process_start"]) >= planned_start
    ]
    assert future_lots, "the fixture must have future rows for this test to mean anything"
    # Mutate the stored documents directly, so the future rows really do change.
    future_lots = [db.lots.find_one({"lot_id": lot["lot_id"]}) for lot in future_lots]
    originals = {lot["_id"]: copy.deepcopy(lot) for lot in future_lots}

    try:
        for lot in future_lots:
            db.lots.update_one(
                {"_id": lot["_id"]},
                {
                    "$set": {
                        "yield": 1.0,
                        "risk_level": "HIGH",
                        "risk_probability": 0.99,
                        "defect_density": 0.9,
                    }
                },
            )
        mutated = upcoming_lot_risk.score_planned_run(db, planned_run["equipment_id"], planned_run["planned_start"])
        assert mutated == baseline, "a future lot changed the pre-run estimate"
    finally:
        for lot_id, original in originals.items():
            db.lots.replace_one({"_id": lot_id}, original)


def test_prior_mutation_does_change_the_score(db, planned_run):
    """Positive control: the prior window IS read, so the test above is not vacuous."""
    baseline = upcoming_lot_risk.score_planned_run(db, planned_run["equipment_id"], planned_run["planned_start"])
    source_lot_id = baseline["sources"]["state_source_lot_id"]
    original = copy.deepcopy(db.lots.find_one({"lot_id": source_lot_id}))

    try:
        db.lots.update_one({"lot_id": source_lot_id}, {"$set": {"yield": 1.0}})
        mutated = upcoming_lot_risk.score_planned_run(db, planned_run["equipment_id"], planned_run["planned_start"])
        assert mutated["risk_probability"] != baseline["risk_probability"]
    finally:
        db.lots.replace_one({"_id": original["_id"]}, original)


def test_long_prior_history_is_not_silently_ignored(db, planned_run):
    """Prior yields are capped by the documented window, not by whatever fits."""
    result = upcoming_lot_risk.score_planned_run(db, planned_run["equipment_id"], planned_run["planned_start"])
    assert result["sources"]["prior_yield_window"] <= upcoming_lot_risk.DEFAULT_PRIOR_YIELD_WINDOW
    assert result["sources"]["n_prior_lots_on_tool"] >= result["sources"]["prior_yield_window"]


# --------------------------------------------------------------------------- refusals
def test_tool_with_no_prior_run_is_refused_not_faked(db):
    lots = LotRepository(db).find_many({}, sort=[("process_start", 1)], limit=1)
    with pytest.raises(ValidationFailedError):
        upcoming_lot_risk.score_planned_run(db, lots[0]["equipment"], lots[0]["process_start"])


def test_unknown_equipment_raises_not_found(db, planned_run):
    with pytest.raises(NotFoundError):
        upcoming_lot_risk.score_planned_run(db, "TOOL_DOES_NOT_EXIST", planned_run["planned_start"])


# --------------------------------------------------------------------------- upcoming queue
def test_upcoming_list_is_risk_sorted_labelled_and_honest_about_being_derived(db):
    result = upcoming_lot_risk.upcoming_lot_risks(db, limit=10)

    assert result["lots"], "a seeded fab must produce at least one scorable tool"
    assert result["queue_note"] and "not a production" in result["queue_note"]
    assert result["label"]
    assert len(result["lots"]) == min(result["n_tools_scored"], 10)

    probabilities = [entry["risk_probability"] for entry in result["lots"]]
    assert probabilities == sorted(probabilities, reverse=True), "the queue must be risk-sorted"

    scored_equipment = {entry["equipment_id"] for entry in result["lots"]}
    skipped_equipment = {entry["equipment_id"] for entry in result["skipped"]}
    assert not (scored_equipment & skipped_equipment), "a tool cannot be both scored and skipped"

    for entry in result["lots"]:
        assert entry["risk_level"] in {"LOW", "MEDIUM", "HIGH"}
        assert entry["top_factors"]
        assert isinstance(entry["similar_lots"], list)


def test_upcoming_default_planned_start_is_the_dataset_now(db):
    result = upcoming_lot_risk.upcoming_lot_risks(db, limit=3)
    assert result["planned_start"] == upcoming_lot_risk.dataset_now(db).isoformat()


# --------------------------------------------------------------------------- API contract
def test_post_lot_risk_returns_a_labelled_estimate(client, planned_run):
    response = client.post(
        "/api/lot-risk",
        json={"equipment_id": planned_run["equipment_id"], "planned_start": str(planned_run["planned_start"])},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["risk_level"] in {"LOW", "MEDIUM", "HIGH"}
    assert data["proxy_note"] and data["label"]
    assert data["sources"]["cutoff"].startswith("every input row")


def test_post_lot_risk_defaults_planned_start_to_dataset_now(client, db, planned_run):
    response = client.post("/api/lot-risk", json={"equipment_id": planned_run["equipment_id"]})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["planned_start"] == upcoming_lot_risk.dataset_now(db).isoformat()


def test_post_lot_risk_rejects_unknown_equipment_and_bad_body(client):
    response = client.post("/api/lot-risk", json={"equipment_id": "TOOL_DOES_NOT_EXIST"})
    assert response.status_code == 404, response.text

    response = client.post("/api/lot-risk", json={"planned_start": "2026-09-14T00:00:00Z"})
    assert response.status_code == 422, "equipment_id is required"


def test_lot_risk_endpoints_require_auth(db, planned_run):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as anonymous:
        assert anonymous.post("/api/lot-risk", json={"equipment_id": planned_run["equipment_id"]}).status_code == 401
        assert anonymous.get("/api/lot-risk/upcoming").status_code == 401
    app.dependency_overrides.clear()


def test_get_upcoming_endpoint_matches_the_service(client, db):
    response = client.get("/api/lot-risk/upcoming", params={"limit": 5})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["lots"]) <= 5
    service = upcoming_lot_risk.upcoming_lot_risks(db, limit=5)
    assert [e["equipment_id"] for e in data["lots"]] == [e["equipment_id"] for e in service["lots"]]


# --------------------------------------------------------------------------- PHASE 4 completion
def test_future_maintenance_and_predictions_do_not_change_the_score(db, planned_run):
    """Extends the lots-only proof to the other two inputs a pre-run estimate reads:
    maintenance events (equipment state) and stored feature snapshots (the
    similarity pool and its scaler). Rows at/after the planned start must be inert."""
    baseline = upcoming_lot_risk.score_planned_run(db, planned_run["equipment_id"], planned_run["planned_start"])
    planned_start = upcoming_lot_risk.parse_time(planned_run["planned_start"])

    future_ids = [
        lot["lot_id"]
        for lot in LotRepository(db).find_many({})
        if upcoming_lot_risk.parse_time(lot["process_start"]) >= planned_start
    ]
    pred_originals = [copy.deepcopy(p) for p in db.predictions.find({"lot_id": {"$in": future_ids}})]
    future_maint = [
        m for m in db.maintenance.find({"equipment_id": planned_run["equipment_id"]})
        if upcoming_lot_risk.parse_time(m["timestamp"]) >= planned_start
    ]
    maint_originals = [copy.deepcopy(m) for m in future_maint]
    injected_id = None
    try:
        for p in pred_originals:
            snapshot = {k: (v * 50 if isinstance(v, float) else v) for k, v in p["features_snapshot"].items()}
            db.predictions.update_one({"_id": p["_id"]}, {"$set": {"features_snapshot": snapshot}})
        for m in future_maint:
            db.maintenance.update_one({"_id": m["_id"]}, {"$set": {"event_type": "unscheduled_repair"}})
        # A maintenance event exactly AT the planned start is not "before" it either.
        injected_id = db.maintenance.insert_one(
            {"equipment_id": planned_run["equipment_id"], "event_type": "unscheduled_repair",
             "timestamp": planned_run["planned_start"], "event_id": "MAINT_TEST_AT_START"}
        ).inserted_id
        mutated = upcoming_lot_risk.score_planned_run(db, planned_run["equipment_id"], planned_run["planned_start"])
        assert mutated == baseline, "a future prediction or maintenance row changed the pre-run estimate"
    finally:
        for p in pred_originals:
            db.predictions.replace_one({"_id": p["_id"]}, p)
        for m in maint_originals:
            db.maintenance.replace_one({"_id": m["_id"]}, m)
        if injected_id is not None:
            db.maintenance.delete_one({"_id": injected_id})


def test_history_features_are_rebuilt_as_of_the_planned_start(db, planned_run):
    """The proxy lot's stored history ends one run earlier; the planned run's
    previous-lot yield must be the proxy lot's OWN yield (training's shift(1))."""
    result = upcoming_lot_risk.score_planned_run(db, planned_run["equipment_id"], planned_run["planned_start"])
    source = LotRepository(db).get_by_lot_id(result["sources"]["state_source_lot_id"])
    state = result["equipment_state"]

    assert state["previous_lot_yield"] == pytest.approx(float(source["yield"]))
    assert 0.0 <= state["equipment_prev10_low_yield_rate"] <= 1.0
    assert state["maintenance_days_since_last"] >= 0.0
    if state["last_maintenance"]:
        assert upcoming_lot_risk.parse_time(state["last_maintenance"]["timestamp"]) < upcoming_lot_risk.parse_time(
            planned_run["planned_start"]
        )
    assert "recomputed" in result["sources"]["history_features"]


def test_reports_a_calibrated_low_yield_probability(db, planned_run):
    result = upcoming_lot_risk.score_planned_run(db, planned_run["equipment_id"], planned_run["planned_start"])
    assert 0.0 <= result["probability_low_yield"] <= 1.0
    assert result["probability_calibration"] in {"isotonic", "sigmoid"}
    assert result["risk_level"] in {"LOW", "MEDIUM", "HIGH"}


def test_planned_recipe_drives_the_process_step_and_is_reported(db, planned_run):
    source_step = upcoming_lot_risk.score_planned_run(
        db, planned_run["equipment_id"], planned_run["planned_start"]
    )["process_step"]
    other = db.recipes.find_one({"process_step": {"$ne": source_step}})
    assert other, "the generated fab must have recipes on more than one step"

    result = upcoming_lot_risk.score_planned_run(
        db, planned_run["equipment_id"], planned_run["planned_start"], recipe_id=other["recipe_id"]
    )
    assert result["process_step"] == other["process_step"]
    assert result["process_step_source"] == "planned recipe"
    history = result["recipe_history"]
    assert history["recipe_id"] == other["recipe_id"]
    planned_start = upcoming_lot_risk.parse_time(planned_run["planned_start"])
    expected = [
        lot for lot in LotRepository(db).find_many({"recipe_id": other["recipe_id"]})
        if upcoming_lot_risk.parse_time(lot["process_start"]) < planned_start
    ]
    assert history["n_prior_lots"] == len(expected)
    for match in result["similar_lots"]:
        assert LotRepository(db).get_by_lot_id(match["lot_id"])["process_step"] == other["process_step"]


def test_recipe_step_conflict_and_unknown_recipe_are_refused(db, planned_run):
    recipe = db.recipes.find_one({})
    wrong_step = "NOT_" + recipe["process_step"]
    with pytest.raises(ValidationFailedError):
        upcoming_lot_risk.score_planned_run(
            db, planned_run["equipment_id"], planned_run["planned_start"],
            recipe_id=recipe["recipe_id"], process_step=wrong_step,
        )
    with pytest.raises(NotFoundError):
        upcoming_lot_risk.score_planned_run(
            db, planned_run["equipment_id"], planned_run["planned_start"], recipe_id="REC_DOES_NOT_EXIST"
        )


def test_upcoming_entries_carry_a_main_reason(db):
    result = upcoming_lot_risk.upcoming_lot_risks(db, limit=5)
    for entry in result["lots"]:
        assert entry["main_reason"] and isinstance(entry["main_reason"], str)
