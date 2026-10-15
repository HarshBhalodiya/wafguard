"""
Phase 5 (brief P4) — evidence-backed recommendations.

Pins the four properties the brief asks for: the intervention table exists and
is marked synthetic; actions come only from the knowledge-base file; every
statistic is a count over recorded cases strictly before the lot; and small
samples are flagged.
"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.knowledge import all_actions, load_engineering_kb
from app.main import app
from app.recommend import engine
from app.simulator.interventions import INTERVENTION_COLUMNS, generate_interventions


# --------------------------------------------------------------------------- the table
def test_intervention_table_is_synthetic_and_hides_ground_truth(sim_tables):
    table = sim_tables["interventions"]
    assert not table.empty, "the generated fab has low-yield lots, so it must have interventions"
    assert list(table.columns) == INTERVENTION_COLUMNS
    assert table["is_simulated"].all()
    assert set(table["data_source"]) == {"synthetic_intervention_model"}
    assert "true_fault" not in table.columns and "mechanism" not in table.columns
    assert table["intervention_id"].is_unique


def test_every_intervention_uses_a_knowledge_base_action(sim_tables):
    kb_actions = all_actions()
    table = sim_tables["interventions"]
    for row in table.itertuples(index=False):
        assert row.action_id in kb_actions
        assert kb_actions[row.action_id]["cause"] == row.diagnosed_cause


def test_interventions_follow_low_yield_lots_and_are_consistent(sim_tables):
    table = sim_tables["interventions"]
    lots = sim_tables["lots"].set_index("lot_id")
    threshold = 85.0
    for row in table.itertuples(index=False):
        trigger = lots.loc[row.trigger_lot_id]
        assert trigger["yield"] < threshold
        assert pd.Timestamp(row.timestamp) > pd.Timestamp(trigger["process_end"])
        assert row.yield_change == pytest.approx(row.yield_after - row.yield_before, abs=0.011)
        assert row.recovered == (row.yield_after >= threshold)


def test_generation_is_deterministic_and_does_not_mutate_inputs(sim_tables):
    lots = sim_tables["lots"].copy()
    gt = sim_tables["ground_truth"].copy()
    a = generate_interventions(lots, gt, seed=123, low_yield_threshold=85.0)
    b = generate_interventions(lots, gt, seed=123, low_yield_threshold=85.0)
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(lots, sim_tables["lots"])
    pd.testing.assert_frame_equal(gt, sim_tables["ground_truth"])


def test_correct_actions_recover_more_often_than_misdirected_ones(sim_tables):
    """The simulator's causal story is visible in the data: when the engineer's
    diagnosis fits the hidden fault, recovery is more likely. (Evaluation-side
    check — it reads ground truth, which only tests/evaluation may do.)"""
    from app.simulator.interventions import FAULT_DIAGNOSES

    table = sim_tables["interventions"].merge(
        sim_tables["ground_truth"][["lot_id", "true_fault"]], left_on="trigger_lot_id", right_on="lot_id"
    )
    fits = table.apply(lambda r: r["diagnosed_cause"] in FAULT_DIAGNOSES.get(r["true_fault"], []), axis=1)
    if fits.sum() < 5 or (~fits).sum() < 5:
        pytest.skip("too few interventions in the small test dataset to compare")
    assert table.loc[fits, "recovered"].mean() > table.loc[~fits, "recovered"].mean()


# --------------------------------------------------------------------------- the engine
def test_recommendations_are_kb_actions_with_backward_looking_counts(db, incident_lot_id):
    result = engine.recommend_for_lot(db, incident_lot_id)
    assert result["label"].startswith("SYNTHETIC")
    assert result["actions"], "the incident lot must get at least one evidence-backed action"

    lot = db.lots.find_one({"lot_id": incident_lot_id})
    history = engine.prior_interventions(db, lot["process_start"])
    kb = all_actions()
    for action in result["actions"]:
        assert action["action_id"] in kb and kb[action["action_id"]]["cause"] == action["cause"]
        assert action["grounded_in"], "an action must cite the evidence that put its cause on the list"
        expected = [c for c in history if c["diagnosed_cause"] == action["cause"] and c["action_id"] == action["action_id"]]
        assert action["n_similar_cases"] == len(expected)
        assert action["n_recovered"] == sum(1 for c in expected if c["recovered"])
        assert action["recovery_text"] == f"{action['n_recovered']}/{action['n_similar_cases']}"
        for case_id in action["case_ids"]:
            case = db.interventions.find_one({"intervention_id": case_id})
            assert engine._parse_time(case["timestamp"]) < engine._parse_time(lot["process_start"])

    scores = [a["ranking_score"] for a in result["actions"]]
    assert scores == sorted(scores, reverse=True)


def test_future_interventions_are_never_counted(db, incident_lot_id):
    lot = db.lots.find_one({"lot_id": incident_lot_id})
    baseline = engine.recommend_for_lot(db, incident_lot_id)
    action = baseline["actions"][0]
    injected = [
        {
            "intervention_id": f"INT_FUTURE_{i}",
            "trigger_lot_id": incident_lot_id,
            "equipment_id": lot["equipment"],
            "process_step": lot["process_step"],
            "timestamp": "2099-01-01T00:00:00+00:00",
            "diagnosed_cause": action["cause"],
            "action_id": action["action_id"],
            "yield_before": 70.0,
            "yield_after": 99.0,
            "yield_change": 29.0,
            "recovered": True,
        }
        for i in range(5)
    ]
    db.interventions.insert_many(injected)
    try:
        assert engine.recommend_for_lot(db, incident_lot_id) == baseline
    finally:
        db.interventions.delete_many({"intervention_id": {"$regex": "^INT_FUTURE_"}})


def test_small_samples_are_flagged():
    min_cases = load_engineering_kb()["min_cases_for_confidence"]
    few = [{"recovered": True, "yield_change": 5.0, "yield_after": 92.0}] * (min_cases - 1)
    many = [{"recovered": i % 3 != 0, "yield_change": 2.0, "yield_after": 90.0} for i in range(min_cases + 2)]

    stats_few = engine.action_statistics(few, min_cases)
    assert stats_few["small_sample_warning"] and "Small sample" in stats_few["small_sample_warning"]
    assert stats_few["recovery_text"] == f"{min_cases - 1}/{min_cases - 1}"
    assert engine.action_statistics(many, min_cases)["small_sample_warning"] is None
    assert engine.action_statistics([], min_cases)["small_sample_warning"].startswith("No recorded case")
    # Laplace smoothing: a perfect tiny record does not beat a strong large one.
    big = [{"recovered": True, "yield_change": 5.0, "yield_after": 95.0}] * 38 + [{"recovered": False}] * 2
    assert engine.action_statistics(big, min_cases)["smoothed_recovery_rate"] > engine.action_statistics(
        few[:1], min_cases
    )["smoothed_recovery_rate"]


# --------------------------------------------------------------------------- the API
def test_recommendations_endpoint(db, incident_lot_id):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            assert client.get(f"/api/recommendations/{incident_lot_id}").status_code == 401
            token = client.post("/api/auth/login", json={"username": "viewer", "password": "wafguard-viewer"}).json()
            client.headers["Authorization"] = f"Bearer {token['data']['access_token']}"
            response = client.get(f"/api/recommendations/{incident_lot_id}")
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["actions"] and "SYNTHETIC" in data["label"]
            assert client.get("/api/recommendations/LOT_DOES_NOT_EXIST").status_code == 404
    finally:
        app.dependency_overrides.clear()
