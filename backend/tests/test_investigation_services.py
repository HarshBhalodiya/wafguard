"""
Integration-style tests for the investigation services (root-cause,
recommendations, similarity, what-if) against the seeded+trained
mongomock database from conftest.py, anchored on the deterministic
LOT_10901 demo incident so assertions can check real, specific behavior
rather than just "it didn't crash".
"""
from app.services import recommendation_service, rootcause_service, similarity_service, simulation_service


def test_seed_produced_expected_scale(db):
    assert db.lots.count_documents({}) == 950
    assert db.equipment.count_documents({}) == 30
    assert db.wafers.count_documents({}) > 0
    assert db.users.count_documents({}) == 5


def test_incident_lot_backfilled_as_high_risk(db, incident_lot_id):
    lot = db.lots.find_one({"lot_id": incident_lot_id})
    assert lot is not None
    assert lot["predicted_yield"] is not None
    assert lot["risk_level"] in ("MEDIUM", "HIGH")  # the drift story should never backfill this as LOW


def test_rootcause_ranks_rf_power_first_for_incident(db, incident_lot_id):
    result = rootcause_service.compute_root_cause(db, incident_lot_id)
    assert result["candidates"], "expected at least one root-cause candidate"
    top = result["candidates"][0]
    assert top["candidate"] == "RF Power"
    assert top["confidence"] in ("MEDIUM", "HIGH")
    assert len(top["evidence"]) > 0
    for evidence in top["evidence"]:
        assert evidence["kind"] in ("FACT", "MODEL_OUTPUT", "INFERENCE", "RECOMMENDATION")
    # every candidate score must be a bounded 0-1 weighted combination
    for candidate in result["candidates"]:
        assert 0.0 <= candidate["score"] <= 1.0


def test_rootcause_unknown_lot_raises(db):
    from app.utils.exceptions import NotFoundError

    try:
        rootcause_service.compute_root_cause(db, "LOT_DOES_NOT_EXIST")
        assert False, "expected NotFoundError"
    except NotFoundError:
        pass


def test_recommendations_reference_real_equipment(db, incident_lot_id):
    lot = db.lots.find_one({"lot_id": incident_lot_id})
    result = recommendation_service.generate_recommendations(db, incident_lot_id)
    assert result["recommendations"], "expected at least one recommendation step"
    assert any(lot["equipment"] in step for step in result["recommendations"])
    assert result["recommendation_type"] == "Suggested engineering investigation"
    # terminology discipline: never phrase this as an instruction to change the recipe
    joined = " ".join(result["recommendations"]).lower()
    assert "change the recipe" not in joined
    assert "automatically" not in joined


def test_similarity_returns_same_process_step_neighbors(db, incident_lot_id):
    lot = db.lots.find_one({"lot_id": incident_lot_id})
    result = similarity_service.find_similar_lots(db, incident_lot_id, k=5)
    assert 1 <= len(result["similar_lots"]) <= 5
    returned_ids = [s["lot_id"] for s in result["similar_lots"]]
    assert incident_lot_id not in returned_ids  # must not return itself
    assert len(returned_ids) == len(set(returned_ids))  # no duplicates
    for neighbor in result["similar_lots"]:
        assert 0.0 <= neighbor["similarity_score"] <= 1.0
        neighbor_lot = db.lots.find_one({"lot_id": neighbor["lot_id"]})
        assert neighbor_lot["process_step"] == lot["process_step"]


def test_what_if_lowering_rf_power_improves_outcome(db, incident_lot_id):
    result = simulation_service.run_what_if(db, incident_lot_id, {"rf_power": 858})
    assert result["after"]["predicted_yield"] >= result["before"]["predicted_yield"]
    assert result["after"]["risk_probability"] <= result["before"]["risk_probability"]
    assert result["delta"]["predicted_yield"] == round(
        result["after"]["predicted_yield"] - result["before"]["predicted_yield"], 2
    )


def test_what_if_rejects_unknown_parameter(db, incident_lot_id):
    from app.utils.exceptions import ValidationFailedError

    try:
        simulation_service.run_what_if(db, incident_lot_id, {"not_a_real_parameter": 1})
        assert False, "expected ValidationFailedError"
    except ValidationFailedError:
        pass


def test_what_if_never_persists_changes(db, incident_lot_id):
    before_doc = dict(db.lots.find_one({"lot_id": incident_lot_id}))
    simulation_service.run_what_if(db, incident_lot_id, {"rf_power": 700})
    after_doc = dict(db.lots.find_one({"lot_id": incident_lot_id}))
    assert before_doc == after_doc
